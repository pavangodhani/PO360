from app.core.config import MailboxConfig
from app.mail.imap_client import ImapClient


def test_imap_client_receives_mark_seen_separately():
    mailbox = MailboxConfig(
        name="Demo", email="demo@example.com", password="secret",
        imap_host="imap.example.com"
    )
    client = ImapClient(mailbox, mark_seen=True)
    assert client.mark_seen is True
    assert not hasattr(mailbox, "mark_seen")
