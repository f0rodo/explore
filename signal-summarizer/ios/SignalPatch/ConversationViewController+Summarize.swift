//
// The Summarize button in a conversation's navigation bar.
//
// Wiring: add one line to `updateBarButtonItems()` in
// ConversationViewController+UI.swift — see ios/SignalPatch/navbar.patch.
//

import UIKit
import SignalServiceKit

extension ConversationViewController {

    var summarizeBarButtonItem: UIBarButtonItem {
        let item = UIBarButtonItem(
            image: UIImage(systemName: "text.line.3.summary") ?? UIImage(systemName: "sparkles"),
            style: .plain,
            target: self,
            action: #selector(didTapSummarize)
        )
        item.accessibilityLabel = "Summarize this conversation"
        return item
    }

    @objc
    func didTapSummarize() {
        let picker = UIAlertController(
            title: "Summarize",
            message: "Everything stays on this device.",
            preferredStyle: .actionSheet
        )
        for window: SummarizeWindow in [.lastHours(12), .lastHours(24), .lastMessages(50)] {
            picker.addAction(UIAlertAction(title: window.description.capitalizedFirst, style: .default) { [weak self] _ in
                self?.summarizeConversation(window: window)
            })
        }
        picker.addAction(UIAlertAction(title: "Cancel", style: .cancel))
        picker.popoverPresentationController?.barButtonItem = navigationItem.rightBarButtonItems?.last
        present(picker, animated: true)
    }

    private func summarizeConversation(window: SummarizeWindow) {
        let thread = self.thread
        let threadUniqueId = thread.uniqueId
        let conversationName = SSKEnvironment.shared.databaseStorageRef.read { tx in
            SSKEnvironment.shared.contactManagerRef.displayName(for: thread, transaction: tx)
        }

        Task { @MainActor in
            // The database read can be slow on a long thread; keep it off the
            // main actor so the conversation stays responsive.
            let entries = await Task.detached(priority: .userInitiated) {
                SignalTranscriptSource.loadTranscript(threadUniqueId: threadUniqueId, window: window)
            }.value

            guard !entries.isEmpty else {
                let alert = UIAlertController(
                    title: "Nothing to summarize",
                    message: "There are no messages in \(window.description).",
                    preferredStyle: .alert
                )
                alert.addAction(UIAlertAction(title: "OK", style: .default))
                self.present(alert, animated: true)
                return
            }

            let summaryVC = SummaryViewController(
                conversationName: conversationName,
                windowDescription: window.description,
                messageCount: entries.count
            )
            self.present(UINavigationController(rootViewController: summaryVC), animated: true)

#if canImport(FoundationModels)
            guard #available(iOS 26.0, *) else {
                summaryVC.show(error: SummarizerError.engineUnavailable(
                    "On-device summarizing needs iOS 26 or later."
                ))
                return
            }

            let summarizer = ConversationSummarizer(engine: FoundationModelsEngine())
            summaryVC.showProgress(completed: 0, total: summarizer.estimateCalls(for: entries))

            // Progress arrives from whatever thread the model finishes on, so it
            // is funnelled through a stream and rendered on the main actor.
            let (progress, progressSink) = AsyncStream<(Int, Int)>.makeStream()
            let rendering = Task { @MainActor in
                for await (completed, total) in progress {
                    summaryVC.showProgress(completed: completed, total: total)
                }
            }

            do {
                let summary = try await summarizer.summarize(
                    entries,
                    conversationName: conversationName,
                    windowDescription: window.description,
                    onProgress: { completed, total in progressSink.yield((completed, total)) }
                )
                progressSink.finish()
                await rendering.value
                summaryVC.show(summary: summary)
            } catch {
                progressSink.finish()
                await rendering.value
                summaryVC.show(error: error)
            }
#else
            summaryVC.show(error: SummarizerError.engineUnavailable(
                "This build has no on-device model. Build with the iOS 26 SDK, or "
                    + "supply another SummarizationEngine."
            ))
#endif
        }
    }
}

private extension String {
    var capitalizedFirst: String {
        guard let first else { return self }
        return first.uppercased() + dropFirst()
    }
}
