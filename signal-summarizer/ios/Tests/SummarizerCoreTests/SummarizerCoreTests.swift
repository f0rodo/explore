import Testing
import Foundation
@testable import SummarizerCore

// Run with `swift test` from ios/. These cover the parts that do not need a
// model or a device: rendering, chunking, and the map-reduce orchestration.

private func entry(
    _ body: String,
    sender: String = "Alice",
    minutesAgo: Int = 0,
    quotedText: String? = nil,
    attachments: Int = 0
) -> TranscriptEntry {
    TranscriptEntry(
        sender: sender,
        date: Date(timeIntervalSince1970: 1_700_000_000 - Double(minutesAgo * 60)),
        body: body,
        quotedText: quotedText,
        attachmentCount: attachments
    )
}

/// Records every prompt it is asked to answer.
private actor RecordingEngine: SummarizationEngine {
    private(set) var prompts: [String] = []
    private var replies: [String]

    init(replies: [String] = []) {
        self.replies = replies
    }

    func respond(instructions: String, prompt: String) async throws -> String {
        prompts.append(prompt)
        if replies.isEmpty { return "summary \(prompts.count)" }
        return replies.removeFirst()
    }

    var recorded: [String] { prompts }
}

@Suite("Rendering")
struct RenderingTests {
    @Test func rendersQuotesAndAttachments() {
        let line = entry("yes", quotedText: "Bob: thursday?", attachments: 1).rendered()
        #expect(line.contains("Alice:"))
        #expect(line.contains("(replying to Bob: thursday?)"))
        #expect(line.contains("yes"))
        #expect(line.contains("[1 attachment]"))
    }

    @Test func pluralizesAttachments() {
        #expect(entry("", attachments: 2).rendered().contains("[2 attachments]"))
    }

    @Test func dropsOldestLinesOverBudget() {
        let entries = (0..<50).map { entry("message \($0)", minutesAgo: 50 - $0) }
        let rendered = Transcript.render(entries, maxCharacters: 200)
        #expect(rendered.hasPrefix("[... "))
        #expect(rendered.contains("earlier messages omitted"))
        #expect(rendered.contains("message 49"))
        #expect(!rendered.contains("message 0 "))
    }
}

@Suite("Chunking")
struct ChunkingTests {
    @Test func groupsWithinBudget() {
        let entries = (0..<10).map { entry(String(repeating: "x", count: 40), minutesAgo: 10 - $0) }
        let chunks = Transcript.chunk(entries, budget: 200)
        #expect(chunks.count > 1)
        #expect(chunks.reduce(0) { $0 + $1.count } == 10)
        #expect(chunks.first?.first == entries.first)
        #expect(chunks.last?.last == entries.last)
    }

    @Test func keepsOneOversizedMessage() {
        let entries = [entry(String(repeating: "x", count: 5_000))]
        #expect(Transcript.chunk(entries, budget: 100).count == 1)
    }
}

@Suite("Summarizing")
struct SummarizingTests {
    let config = SummarizerConfiguration(transcriptBudget: 300)

    @Test func singlePassIncludesTheTranscript() async throws {
        let engine = RecordingEngine(replies: ["Alice moved standup."])
        let summarizer = ConversationSummarizer(engine: engine, configuration: config)

        let summary = try await summarizer.summarize(
            [entry("standup moves to 10")],
            conversationName: "Launch",
            windowDescription: "the last 24 hours"
        )

        #expect(summary == "Alice moved standup.")
        let prompt = try #require(await engine.recorded.first)
        #expect(prompt.contains("Conversation: Launch"))
        #expect(prompt.contains("Window: the last 24 hours"))
        #expect(prompt.contains("Alice: standup moves to 10"))
    }

    @Test func emptyWindowThrows() async {
        let summarizer = ConversationSummarizer(engine: RecordingEngine(), configuration: config)
        await #expect(throws: SummarizerError.self) {
            _ = try await summarizer.summarize([], conversationName: "Launch", windowDescription: "today")
        }
    }

    @Test func longWindowIsChunkedThenCombined() async throws {
        let engine = RecordingEngine()
        let summarizer = ConversationSummarizer(engine: engine, configuration: config)
        let entries = (0..<12).map { entry(String(repeating: "y", count: 60), minutesAgo: 12 - $0) }

        _ = try await summarizer.summarize(
            entries,
            conversationName: "Launch",
            windowDescription: "the last 24 hours"
        )

        let prompts = await engine.recorded
        let chunkPrompts = prompts.filter { $0.contains("This is part") }
        let combinePrompts = prompts.filter { $0.contains("consecutive parts") }
        #expect(chunkPrompts.count >= 2)
        #expect(combinePrompts.count == 1)
        // the combine step sees partial summaries, not the raw transcript
        #expect(combinePrompts[0].contains("--- part 1 ---"))
        #expect(!combinePrompts[0].contains("yyyy"))
    }

    @Test func estimateMatchesTheCallsActuallyMade() async throws {
        let engine = RecordingEngine()
        let summarizer = ConversationSummarizer(engine: engine, configuration: config)
        let entries = (0..<12).map { entry(String(repeating: "y", count: 60), minutesAgo: 12 - $0) }
        let estimate = summarizer.estimateCalls(for: entries)

        _ = try await summarizer.summarize(
            entries,
            conversationName: "Launch",
            windowDescription: "the last 24 hours"
        )

        #expect(estimate > 2)
        #expect(await engine.recorded.count == estimate)
    }

    @Test func progressIsReported() async throws {
        let engine = RecordingEngine()
        let summarizer = ConversationSummarizer(engine: engine, configuration: config)
        let entries = (0..<12).map { entry(String(repeating: "y", count: 60), minutesAgo: 12 - $0) }

        final class Box: @unchecked Sendable { var updates: [(Int, Int)] = [] }
        let box = Box()

        _ = try await summarizer.summarize(
            entries,
            conversationName: "Launch",
            windowDescription: "the last 24 hours",
            onProgress: { done, total in box.updates.append((done, total)) }
        )

        #expect(box.updates.first?.0 == 1)
        #expect(box.updates.last?.0 == box.updates.last?.1)
    }
}
