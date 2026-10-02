"""
Step-by-step help for publishing to Cloudflare R2, shown from the Publish
window's Destination tab. Plain HTML in a QTextBrowser; the links open in
the system browser. The plugin never creates, exposes or deletes anything in
the Cloudflare account: these are the steps the user does once, by hand.
"""

from qgis.PyQt.QtCore import QCoreApplication, QUrl
from qgis.PyQt.QtGui import QDesktopServices
from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QPushButton, QTextBrowser, QVBoxLayout

DASHBOARD_R2 = "https://dash.cloudflare.com/?to=/:account/r2/overview"
DASHBOARD_TOKENS = "https://dash.cloudflare.com/?to=/:account/r2/api-tokens"


def tr(text: str) -> str:
    return QCoreApplication.translate("R2Guide", text)


# Field help (tooltips), also used by the guide.
FIELD_HELP = {
    "account": "R2 → Overview → Account Details → Account ID (32 characters). You can paste the "
               "S3 API URL or the address of the dashboard page instead: the id (and the bucket) "
               "is taken from it.",
    "endpoint": "Leave empty for normal buckets (it is derived from the account id). Only for "
                "buckets in a jurisdiction (EU, FedRAMP): bucket → Settings → S3 API.",
    "bucket": "The bucket's name exactly as in R2 → Overview (lowercase letters, digits, "
              "hyphens).",
    "prefix": "A folder inside the bucket for this map, e.g. maps/arlo. Several maps can share "
              "one bucket with different prefixes. Empty: maps/<publication name>.",
    "public": "The address people open: the custom domain connected to the bucket "
              "(bucket → Settings → Custom Domains), e.g. https://maps.example.com. Not the S3 "
              "API URL.",
    "auth": "Keys saved encrypted in QGIS (protected by the QGIS master password). User name = "
            "Access Key ID, password = Secret Access Key of an R2 API token.",
    "session": "Keys used only while this window is open (not saved anywhere). Use “Save keys in "
               "QGIS…” to keep them encrypted for next time.",
}


def guide_html() -> str:
    steps = [
        (tr("Open R2"),
         tr("Sign in at <a href='{r2}'>dash.cloudflare.com</a> and open <b>R2 Object "
            "Storage</b>. The first time, Cloudflare asks you to activate R2 (there is a free "
            "monthly allowance; it may ask for a payment method). Downloads of your map are not "
            "charged as egress.").format(r2=DASHBOARD_R2)),
        (tr("Account id"),
         tr("On the R2 overview page, <b>Account Details</b> shows the <b>Account ID</b> (32 "
            "characters) and the <b>S3 API</b> address "
            "<code>https://&lt;account id&gt;.r2.cloudflarestorage.com</code>. Copy either one "
            "into <b>Cloudflare account id</b>; the plugin takes the id from the address. Leave "
            "<b>S3 API endpoint</b> empty.")),
        (tr("Bucket"),
         tr("<b>Create bucket</b> → a name such as <code>maps</code> (lowercase letters, digits, "
            "hyphens), location <i>Automatic</i> → <b>Create bucket</b>. Enter the name in "
            "<b>Bucket</b>. (Pasting the bucket's S3 API address, "
            "<code>…r2.cloudflarestorage.com/maps</code>, fills both fields.)")),
        (tr("Prefix"),
         tr("Choose a folder in the bucket for this map, e.g. <code>maps/arlo</code>, and enter "
            "it in <b>Prefix in the bucket</b>. One bucket can hold several maps, each with its "
            "own prefix. The plugin only ever writes inside this prefix.")),
        (tr("Public address (custom domain)"),
         tr("Open the bucket → <b>Settings</b> → <b>Custom Domains</b> → <b>Add</b> → enter a "
            "subdomain of a domain that is on Cloudflare in the same account, e.g. "
            "<code>maps.example.com</code> → <b>Continue</b> → <b>Connect Domain</b>. Cloudflare "
            "adds the DNS record itself; wait until the domain shows <i>Active</i>. Enter "
            "<code>https://maps.example.com</code> in <b>Public base URL</b>.<br>"
            "<i>Only for a quick test:</i> the bucket's <b>Public Development URL</b> "
            "(<code>…r2.dev</code>) also works, but Cloudflare rate-limits it; do not share it.")),
        (tr("API token (the keys)"),
         tr("R2 overview → <b>Account Details</b> → <b>API Tokens</b> → <b>Manage</b> "
            "(<a href='{tokens}'>direct link</a>) → <b>Create API token</b> (an Account token "
            "keeps working if your user leaves the account). Permission: <b>Object Read &amp; "
            "Write</b>; <b>Specify bucket(s)</b>: only your map bucket; TTL: forever. "
            "<b>Create</b>. Copy the <b>Access Key ID</b> and the <b>Secret Access Key</b> now: "
            "Cloudflare shows the secret only once.").format(tokens=DASHBOARD_TOKENS)),
        (tr("Keys in QGIS"),
         tr("Paste the two keys into <b>Or keys for this session only</b> and click <b>Save "
            "keys in QGIS…</b>: they are stored encrypted in QGIS's password store (you may be "
            "asked to create a QGIS master password) and selected under <b>Saved "
            "credentials</b>. The keys are never written into the project file or the "
            "published map.")),
        (tr("Test"),
         tr("Click <b>Test connection</b>. Every line should show ✔. A ✖ says what is wrong "
            "(wrong keys, bucket name, or a token without access to this bucket).")),
        (tr("CORS (usually not needed)"),
         tr("The map opened from your custom domain needs no CORS rule. Only if the map data is "
            "read from another site (e.g. embedded in another web page), click <b>Show CORS "
            "policy</b> and add it under bucket → <b>Settings</b> → <b>CORS policy</b>.")),
        (tr("Publish"),
         tr("<b>Review</b> tab → check what becomes public → tick the approval → "
            "<b>Publish</b>. The map is then at <code>&lt;public base URL&gt;/&lt;prefix&gt;/"
            "</code>. Each publish is a new release; <b>Releases and rollback…</b> switches back "
            "to an earlier one, and <b>Releases to keep</b> limits how many stay in the bucket.")),
    ]
    items = "".join(f"<li><p><b>{title}</b><br>{body}</p></li>" for title, body in steps)
    return (f"<h2>{tr('Publishing to Cloudflare R2')}</h2>"
            f"<p>{tr('One-time setup in the Cloudflare dashboard (about 10 minutes). Afterwards '
                    'publishing is one click.')}</p><ol>{items}</ol>"
            f"<p><i>{tr('Nothing is created, made public or deleted in your Cloudflare account '
                       'by the plugin: it only uploads into the bucket and prefix you enter.')}"
            "</i></p>")


class R2GuideDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(tr("Cloudflare R2: step by step"))
        self.resize(720, 680)
        layout = QVBoxLayout(self)
        self.text = QTextBrowser()
        self.text.setOpenExternalLinks(True)
        self.text.setHtml(guide_html())
        layout.addWidget(self.text)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        dashboard = QPushButton(tr("Open the Cloudflare dashboard"))
        dashboard.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DASHBOARD_R2)))
        buttons.addButton(dashboard, QDialogButtonBox.ButtonRole.ActionRole)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
