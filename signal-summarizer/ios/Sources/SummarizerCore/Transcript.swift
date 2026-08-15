import Foundation

/// One message, flattened out of whatever the chat client stores.
///
/// Nothing in this file knows about Signal or Apple's frameworks, so it builds
/// and tests anywhere.
public struct TranscriptEntry: Sendable, Equatable {
    public let sender: String
    public let date: Date
    public let body: String
    public let quotedText: String?
    public let attachmentCount: Int

    public init(
        sender: String,
        date: Date,
        body: String,
        quotedText: String? = nil,
        attachmentCount: Int = 0
    ) {
        self.sender = sender
        self.date = date
        self.body = body
        self.quotedText = quotedText
        self.attachmentCount = attachmentCount
    }

    /// `[2026-08-15 09:14] Alice: (replying to Bob: thursday?) yes [1 attachment]`
    public func rendered(formatter: DateFormatter = TranscriptEntry.timestampFormatter) -> String {
        var parts = ["[\(formatter.string(from: date))] \(sender):"]
        if let quotedText, !quotedText.isEmpty {
            parts.append("(replying to \(quotedText))")
        }
        if !body.isEmpty {
            parts.append(body)
        }
        if attachmentCount > 0 {
            let noun = attachmentCount == 1 ? "attachment" : "attachments"
            parts.append("[\(attachmentCount) \(noun)]")
        }
        return parts.joined(separator: " ")
    }

    public static let timestampFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm"
        return formatter
    }()
}

public enum Transcript {
    /// Render oldest first, dropping the oldest lines if over the budget.
    public static func render(_ entries: [TranscriptEntry], maxCharacters: Int? = nil) -> String {
        var lines = entries.map { $0.rendered() }
        guard let maxCharacters else { return lines.joined(separator: "\n") }

        var total = lines.reduce(0) { $0 + $1.count + 1 }
        var dropped = 0
        while !lines.isEmpty, total > maxCharacters {
            total -= lines.removeFirst().count + 1
            dropped += 1
        }
        if dropped > 0 {
            lines.insert("[... \(dropped) earlier messages omitted ...]", at: 0)
        }
        return lines.joined(separator: "\n")
    }

    /// Split into consecutive groups that each fit the character budget.
    ///
    /// The on-device model has a small context window, so a day of a busy group
    /// chat does not arrive in one piece. Each group is summarized separately
    /// and the partial summaries are combined afterwards.
    public static func chunk(_ entries: [TranscriptEntry], budget: Int) -> [[TranscriptEntry]] {
        guard !entries.isEmpty else { return [[]] }

        var chunks: [[TranscriptEntry]] = []
        var current: [TranscriptEntry] = []
        var size = 0

        for entry in entries {
            let length = entry.rendered().count + 1
            if !current.isEmpty, size + length > budget {
                chunks.append(current)
                current = []
                size = 0
            }
            current.append(entry)
            size += length
        }
        if !current.isEmpty {
            chunks.append(current)
        }
        return chunks
    }

    /// Group already-summarized parts so each batch fits the budget.
    static func batch(_ texts: [String], budget: Int) -> [[String]] {
        guard !texts.isEmpty else { return [] }

        var batches: [[String]] = []
        var current: [String] = []
        var size = 0

        for text in texts {
            let length = text.count + 2
            if !current.isEmpty, size + length > budget {
                batches.append(current)
                current = []
                size = 0
            }
            current.append(text)
            size += length
        }
        if !current.isEmpty {
            batches.append(current)
        }
        return batches
    }
}
