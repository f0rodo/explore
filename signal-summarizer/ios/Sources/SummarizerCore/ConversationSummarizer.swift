import Foundation

/// Anything that can turn a prompt into text. The app ships one implementation
/// (Apple's on-device model); swap in another without touching the prompts.
public protocol SummarizationEngine: Sendable {
    func respond(instructions: String, prompt: String) async throws -> String
}

public enum SummarizerError: LocalizedError {
    case engineUnavailable(String)
    case emptyResponse
    case nothingToSummarize

    public var errorDescription: String? {
        switch self {
        case .engineUnavailable(let detail): detail
        case .emptyResponse: "The model returned an empty summary."
        case .nothingToSummarize: "There are no messages in that window."
        }
    }
}

public struct SummarizerConfiguration: Sendable {
    /// Characters of transcript per model call. Apple's on-device model has a
    /// small context window shared between prompt and answer, so this is
    /// deliberately conservative; anything larger is summarized in passes.
    public var transcriptBudget: Int
    /// Stop folding partial summaries after this many rounds.
    public var maxReduceRounds: Int

    public init(transcriptBudget: Int = 6_000, maxReduceRounds: Int = 4) {
        self.transcriptBudget = transcriptBudget
        self.maxReduceRounds = maxReduceRounds
    }

    public static let onDevice = SummarizerConfiguration()
}

public struct ConversationSummarizer {
    public static let instructions = """
        You summarize Signal group and direct-message threads for someone who \
        was away and wants to catch up quickly.

        Write plain text. Use short paragraphs and hyphen bullets, not Markdown \
        headings, bold, or tables.

        Cover, in this order, skipping anything the transcript does not contain:
        - What was discussed, grouped by topic rather than replayed message by message.
        - Decisions that were made, and who made them.
        - Action items, each with the person responsible and any deadline mentioned.
        - Open questions still waiting on an answer.

        Name people as the transcript names them. Attribute claims to whoever \
        made them rather than stating them as fact, and say when something was \
        left unresolved rather than guessing at the outcome. Keep small talk to \
        a single line at most. A quiet thread deserves two sentences, not a \
        padded report.

        Reply with the summary itself. No preamble, no commentary about the task.
        """

    public let engine: SummarizationEngine
    public let configuration: SummarizerConfiguration

    public init(engine: SummarizationEngine, configuration: SummarizerConfiguration = .onDevice) {
        self.engine = engine
        self.configuration = configuration
    }

    /// How many model calls this window needs. Cheap: no model involved.
    public func estimateCalls(for entries: [TranscriptEntry]) -> Int {
        guard !entries.isEmpty else { return 0 }
        let chunks = Transcript.chunk(entries, budget: configuration.transcriptBudget).count
        return chunks > 1 ? chunks + 1 : 1
    }

    public func summarize(
        _ entries: [TranscriptEntry],
        conversationName: String,
        windowDescription: String,
        onProgress: (@Sendable (Int, Int) -> Void)? = nil
    ) async throws -> String {
        guard !entries.isEmpty else { throw SummarizerError.nothingToSummarize }

        let budget = configuration.transcriptBudget
        let chunks = Transcript.chunk(entries, budget: budget)
        let total = estimateCalls(for: entries)
        var completed = 0

        func report() {
            completed += 1
            onProgress?(completed, total)
        }

        if chunks.count == 1 {
            let prompt = """
                Conversation: \(conversationName)
                Window: \(windowDescription)
                Messages: \(entries.count)

                Transcript:
                \(Transcript.render(entries, maxCharacters: budget))

                Summarize this conversation.
                """
            let answer = try await engine.respond(instructions: Self.instructions, prompt: prompt)
            report()
            return try nonEmpty(answer)
        }

        var partials: [String] = []
        for (index, chunk) in chunks.enumerated() {
            let prompt = """
                Conversation: \(conversationName)
                Window: \(windowDescription)
                This is part \(index + 1) of \(chunks.count) of one long conversation, \
                in chronological order.

                Transcript:
                \(Transcript.render(chunk, maxCharacters: budget))

                Summarize only this part. Keep the details a final combined summary \
                would need: who said what, decisions, action items, and unresolved \
                questions.
                """
            partials.append(try await engine.respond(instructions: Self.instructions, prompt: prompt))
            report()
        }

        return try await combine(
            partials,
            conversationName: conversationName,
            windowDescription: windowDescription,
            report: report
        )
    }

    /// Fold partial summaries down to one, in rounds if they don't fit at once.
    private func combine(
        _ partials: [String],
        conversationName: String,
        windowDescription: String,
        report: () -> Void
    ) async throws -> String {
        var parts = partials
        for _ in 0..<configuration.maxReduceRounds {
            let batches = Transcript.batch(parts, budget: configuration.transcriptBudget)
            if batches.count <= 1 {
                let prompt = combinePrompt(
                    batches.first ?? parts,
                    conversationName: conversationName,
                    windowDescription: windowDescription,
                    isFinal: true
                )
                let answer = try await engine.respond(instructions: Self.instructions, prompt: prompt)
                report()
                return try nonEmpty(answer)
            }

            var folded: [String] = []
            for batch in batches {
                let prompt = combinePrompt(
                    batch,
                    conversationName: conversationName,
                    windowDescription: windowDescription,
                    isFinal: false
                )
                folded.append(try await engine.respond(instructions: Self.instructions, prompt: prompt))
                report()
            }
            parts = folded
        }
        // Rounds exhausted: the model is padding rather than condensing. Return
        // what we have instead of looping forever.
        return parts.joined(separator: "\n\n")
    }

    private func combinePrompt(
        _ parts: [String],
        conversationName: String,
        windowDescription: String,
        isFinal: Bool
    ) -> String {
        let joined = parts.enumerated()
            .map { "--- part \($0.offset + 1) ---\n\($0.element)" }
            .joined(separator: "\n\n")
        let closing = isFinal
            ? "Combine these into one summary of the whole conversation."
            : "Combine these into one shorter summary, preserving specifics."
        return """
            Conversation: \(conversationName)
            Window: \(windowDescription)

            Below are summaries of consecutive parts of this one conversation, in \
            chronological order.

            \(joined)

            \(closing) Merge duplicates, keep every decision, action item and open \
            question, and do not add anything the parts do not say.
            """
    }

    private func nonEmpty(_ text: String) throws -> String {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { throw SummarizerError.emptyResponse }
        return trimmed
    }
}
