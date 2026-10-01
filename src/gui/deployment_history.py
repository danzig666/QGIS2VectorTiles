"""
Remote releases of a publication: which one is current, rollback to an older
verified release (no re-upload), explicit deletion of old releases (never
the current one) and cleanup of interrupted multipart uploads. Every
destructive action asks first and says that public copies already
downloaded or cached cannot be recalled.
"""

from qgis.PyQt.QtCore import QCoreApplication
from qgis.PyQt.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                                 QMessageBox, QPushButton, QVBoxLayout)

from ..publishing.deployments import apply_retention, retention_plan, rollback
from ..publishing.errors import PublishingError


def tr(text: str) -> str:
    return QCoreApplication.translate("DeploymentHistory", text)


class DeploymentHistoryDialog(QDialog):
    def __init__(self, parent, provider, profile, releases, current):
        super().__init__(parent)
        self.provider, self.profile = provider, profile
        self.setWindowTitle(tr("Releases of {}").format(profile.title))
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        self.label = QLabel()
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.list = QListWidget()
        layout.addWidget(self.list)
        row = QHBoxLayout()
        activate = QPushButton(tr("Make current (rollback)"))
        activate.clicked.connect(self.activate)
        retention = QPushButton(tr("Delete old releases…"))
        retention.clicked.connect(self.retention)
        uploads = QPushButton(tr("Abort interrupted uploads…"))
        uploads.clicked.connect(self.abort_uploads)
        close = QPushButton(tr("Close"))
        close.clicked.connect(self.accept)
        for button in (activate, retention, uploads):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(close)
        layout.addLayout(row)
        self.fill(releases, current)

    def fill(self, releases, current):
        self.list.clear()
        self.current = current
        for release in reversed(releases):
            item = QListWidgetItem(release + ("   ← " + tr("current") if release == current else ""))
            item.setData(256, release)
            self.list.addItem(item)
        self.label.setText(tr("{} release(s) under {}/releases/. Old releases keep old links "
                              "working.").format(len(releases), self.provider.prefix))

    def refresh(self):
        pointer, _ = self.provider.read_pointer()
        self.fill(self.provider.list_releases(), pointer.get("releaseId") if pointer else None)

    def activate(self):
        item = self.list.currentItem()
        if item is None:
            return
        release = item.data(256)
        if QMessageBox.question(self, tr("Rollback"), tr("Make {} the current release?").format(release)) \
                != QMessageBox.StandardButton.Yes:
            return
        try:
            result = rollback(self.provider, self.profile, release)
        except PublishingError as error:
            QMessageBox.warning(self, tr("Rollback"), error.message)
            return
        QMessageBox.information(self, tr("Rollback"), f"{result.state.value}: {result.message or release}")
        self.refresh()

    def retention(self):
        plan = retention_plan(self.provider, self.profile.destination.retention)
        if not plan:
            QMessageBox.information(self, tr("Retention"), tr("Nothing to delete."))
            return
        text = tr("Delete these {} release(s)? Links to them stop working; copies already downloaded "
                  "or cached by others cannot be recalled.\n\n{}").format(len(plan), "\n".join(plan))
        if QMessageBox.question(self, tr("Retention"), text) != QMessageBox.StandardButton.Yes:
            return
        try:
            count = apply_retention(self.provider, plan)
        except PublishingError as error:
            QMessageBox.warning(self, tr("Retention"), error.message)
            return
        QMessageBox.information(self, tr("Retention"), tr("{} object(s) deleted.").format(count))
        self.refresh()

    def abort_uploads(self):
        try:
            uploads = self.provider.incomplete_uploads()
        except PublishingError as error:
            QMessageBox.warning(self, tr("Uploads"), error.message)
            return
        if not uploads:
            QMessageBox.information(self, tr("Uploads"), tr("No interrupted uploads."))
            return
        text = "\n".join(f"{u['key']} ({u['initiated']})" for u in uploads)
        if QMessageBox.question(self, tr("Uploads"), tr("Abort these uploads? A later publish "
                                                        "starts them again.\n\n") + text) \
                != QMessageBox.StandardButton.Yes:
            return
        prefix = self.provider.prefix + "/"
        for upload in uploads:
            if upload["key"].startswith(prefix):
                self.provider.abort_upload(upload["key"][len(prefix):], upload["uploadId"])
        QMessageBox.information(self, tr("Uploads"), tr("Aborted."))
