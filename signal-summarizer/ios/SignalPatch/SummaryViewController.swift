//
// The sheet that shows the summary. Deliberately plain UIKit so it does not
// depend on Signal's view helpers moving around.
//

import UIKit

final class SummaryViewController: UIViewController {
    private let conversationName: String
    private let windowDescription: String
    private let messageCount: Int

    private let statusLabel = UILabel()
    private let spinner = UIActivityIndicatorView(style: .medium)
    private let textView = UITextView()
    private var summaryText: String?

    init(conversationName: String, windowDescription: String, messageCount: Int) {
        self.conversationName = conversationName
        self.windowDescription = windowDescription
        self.messageCount = messageCount
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) is not supported") }

    override func viewDidLoad() {
        super.viewDidLoad()

        view.backgroundColor = .systemBackground
        title = "Summary"
        navigationItem.rightBarButtonItem = UIBarButtonItem(
            barButtonSystemItem: .done,
            target: self,
            action: #selector(dismissSelf)
        )
        navigationItem.leftBarButtonItem = UIBarButtonItem(
            title: "Copy",
            style: .plain,
            target: self,
            action: #selector(copySummary)
        )
        navigationItem.leftBarButtonItem?.isEnabled = false

        statusLabel.font = .preferredFont(forTextStyle: .footnote)
        statusLabel.textColor = .secondaryLabel
        statusLabel.numberOfLines = 0
        statusLabel.text = "\(conversationName) — \(windowDescription), \(messageCount) messages"

        textView.isEditable = false
        textView.font = .preferredFont(forTextStyle: .body)
        textView.adjustsFontForContentSizeCategory = true
        textView.textContainerInset = UIEdgeInsets(top: 12, left: 12, bottom: 12, right: 12)

        spinner.startAnimating()

        let header = UIStackView(arrangedSubviews: [spinner, statusLabel])
        header.axis = .horizontal
        header.spacing = 8
        header.alignment = .center
        header.isLayoutMarginsRelativeArrangement = true
        header.directionalLayoutMargins = .init(top: 12, leading: 16, bottom: 4, trailing: 16)

        let stack = UIStackView(arrangedSubviews: [header, textView])
        stack.axis = .vertical
        stack.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(stack)

        NSLayoutConstraint.activate([
            stack.topAnchor.constraint(equalTo: view.safeAreaLayoutGuide.topAnchor),
            stack.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            stack.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            stack.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
    }

    // MARK: - Updates, all on the main actor

    @MainActor
    func showProgress(completed: Int, total: Int) {
        guard total > 1 else {
            statusLabel.text = "Summarizing \(messageCount) messages on this device…"
            return
        }
        statusLabel.text = "Summarizing \(messageCount) messages on this device… pass \(completed) of \(total)"
    }

    @MainActor
    func show(summary: String) {
        summaryText = summary
        spinner.stopAnimating()
        spinner.isHidden = true
        statusLabel.text = "\(conversationName) — \(windowDescription), \(messageCount) messages"
        textView.text = summary
        navigationItem.leftBarButtonItem?.isEnabled = true
    }

    @MainActor
    func show(error: Error) {
        spinner.stopAnimating()
        spinner.isHidden = true
        statusLabel.text = "Could not summarize"
        textView.text = error.localizedDescription
    }

    @objc private func dismissSelf() {
        dismiss(animated: true)
    }

    @objc private func copySummary() {
        guard let summaryText else { return }
        UIPasteboard.general.string = summaryText
        navigationItem.leftBarButtonItem?.title = "Copied"
    }
}
