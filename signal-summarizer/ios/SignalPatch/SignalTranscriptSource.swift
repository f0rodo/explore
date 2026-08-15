//
// Reads a window of a conversation out of Signal's own database.
//
// This is the only file that touches Signal's internals. Everything it returns
// is a plain `TranscriptEntry`, so the summarizer never sees a Signal type.
//

import Foundation
import SignalServiceKit

public enum SummarizeWindow: Equatable {
    case lastHours(Int)
    case lastMessages(Int)

    public var description: String {
        switch self {
        case .lastHours(let hours):
            return "the last \(hours) hour\(hours == 1 ? "" : "s")"
        case .lastMessages(let count):
            return "the last \(count) message\(count == 1 ? "" : "s")"
        }
    }
}

public enum SignalTranscriptSource {
    /// Hard ceiling on how many messages one summary considers.
    public static let messageCeiling = 300

    /// Load a conversation window, oldest first.
    ///
    /// Takes the thread's unique id rather than the `TSThread` so it can be
    /// called off the main thread without passing a non-Sendable object across
    /// concurrency domains. Runs one database read on the calling thread.
    public static func loadTranscript(
        threadUniqueId: String,
        window: SummarizeWindow
    ) -> [TranscriptEntry] {
        let limit: Int
        let cutoffMs: UInt64?
        switch window {
        case .lastHours(let hours):
            limit = messageCeiling
            let cutoff = Date().addingTimeInterval(-Double(hours) * 3600)
            cutoffMs = UInt64(max(0, cutoff.timeIntervalSince1970) * 1000)
        case .lastMessages(let count):
            limit = min(count, messageCeiling)
            cutoffMs = nil
        }

        return SSKEnvironment.shared.databaseStorageRef.read { tx in
            var entries: [TranscriptEntry] = []
            let finder = InteractionFinder(threadUniqueId: threadUniqueId)

            // `.newest` enumerates newest first, so we walk backwards and stop
            // as soon as we have enough or fall off the end of the window.
            try? finder.enumerateInteractionsForConversationView(
                rowIdFilter: .newest,
                tx: tx
            ) { interaction in
                guard entries.count < limit else { return false }
                if let cutoffMs, interaction.receivedAtTimestamp < cutoffMs { return false }

                if let entry = makeEntry(interaction: interaction, tx: tx) {
                    entries.append(entry)
                }
                return true
            }

            return entries.reversed()
        }
    }

    private static func makeEntry(
        interaction: TSInteraction,
        tx: DBReadTransaction
    ) -> TranscriptEntry? {
        // Only real conversation: no "you joined the group", no calls, no
        // profile-change notices.
        let message: TSMessage
        let sender: String

        switch interaction {
        case let incoming as TSIncomingMessage:
            message = incoming
            sender = SSKEnvironment.shared.contactManagerRef
                .displayName(for: incoming.authorAddress, tx: tx)
                .resolvedValue()
        case let outgoing as TSOutgoingMessage:
            message = outgoing
            sender = "You"
        default:
            return nil
        }

        guard !message.wasRemotelyDeleted, !message.isViewOnceMessage else { return nil }

        let body = (message.body ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
        guard !body.isEmpty else { return nil }

        var quotedText: String?
        if let quoted = message.quotedMessage, let quotedBody = quoted.body, !quotedBody.isEmpty {
            quotedText = quotedBody
        }

        return TranscriptEntry(
            sender: sender,
            date: Date(timeIntervalSince1970: Double(interaction.receivedAtTimestamp) / 1000),
            body: body,
            quotedText: quotedText,
            // Attachment counting needs the attachment store; text-only for now.
            attachmentCount: 0
        )
    }
}
