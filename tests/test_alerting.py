from unittest.mock import patch

from alerting import build_message, fire_all, send_email, send_signal
from gps import LOC_INTERNAL, LOC_MANUAL, LOC_UNSET, Position


class TestBuildMessage:
    def test_with_position(self):
        p = Position(48.8566, 2.3522, altitude=100)
        msg = build_message("Dead Man Switch", "no heartbeat", p)
        assert "FOREST ALERT" in msg
        assert "Dead Man Switch" in msg
        assert "no heartbeat" in msg
        assert "google.com/maps" in msg
        assert "openstreetmap.org" in msg
        assert "Quality" in msg  # quality line is always present

    def test_without_position(self):
        msg = build_message("SOS", "help", None)
        assert "No GPS position available" in msg

    def test_no_trusted_contact_line(self):
        # The removed "Contactez votre personne de confiance" line must stay gone.
        msg = build_message("SOS", "help", None)
        assert "Contactez" not in msg
        assert "personne de confiance" not in msg.lower()
        assert "trusted contact" not in msg.lower()

    def test_stale_position_warning(self):
        p = Position(1.0, 2.0, received_at=0.0)
        msg = build_message("SOS", "help", p)
        assert "Stale position" in msg

    def test_quality_warning_low_precision(self):
        p = Position(1.0, 2.0, precision_bits=11, location_source=LOC_INTERNAL)
        msg = build_message("SOS", "help", p)
        assert "Approximate position" in msg
        assert "low precision" in msg
        assert "km" in msg

    def test_quality_warning_manual_source(self):
        p = Position(1.0, 2.0, location_source=LOC_MANUAL)
        msg = build_message("SOS", "help", p)
        assert "Approximate position" in msg
        assert "manually entered" in msg

    def test_quality_warning_unset_source(self):
        p = Position(1.0, 2.0, location_source=LOC_UNSET)
        msg = build_message("SOS", "help", p)
        assert "Approximate position" in msg
        assert "source unknown" in msg

    def test_good_fix_shows_positive_quality(self):
        p = Position(1.0, 2.0, precision_bits=17, location_source=LOC_INTERNAL,
                     sats_in_view=8)
        msg = build_message("SOS", "help", p)
        assert "Approximate position" not in msg
        assert "Quality" in msg
        assert "GPS fix" in msg

    def test_no_metadata_shows_not_reported(self):
        # Common for local node: no quality fields at all.
        p = Position(1.0, 2.0)
        msg = build_message("SOS", "help", p)
        assert "Quality" in msg
        assert "not reported" in msg

    def test_quality_warning_and_stale_can_coexist(self):
        p = Position(1.0, 2.0, precision_bits=8, location_source=LOC_MANUAL,
                     received_at=0.0)
        msg = build_message("SOS", "help", p)
        assert "Stale position" in msg
        assert "Approximate position" in msg


class TestSendSignal:
    def test_no_contacts_skips(self, make_config):
        assert send_signal(make_config(signal_contacts=[]), "body") is False

    def test_success(self, make_config):
        cfg = make_config(signal_contacts=["+33600000000"])
        with patch("alerting.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {"result": {"timestamp": 123}}
            mock_post.return_value.raise_for_status.return_value = None
            assert send_signal(cfg, "body") is True

    def test_rpc_error_returns_false(self, make_config):
        cfg = make_config(signal_contacts=["+33600000000"])
        with patch("alerting.requests.post") as mock_post:
            mock_post.return_value.json.return_value = {"error": {"code": -1}}
            mock_post.return_value.raise_for_status.return_value = None
            assert send_signal(cfg, "body") is False

    def test_network_exception_returns_false(self, make_config):
        cfg = make_config(signal_contacts=["+33600000000"])
        with patch("alerting.requests.post", side_effect=Exception("boom")):
            assert send_signal(cfg, "body") is False

    def test_unique_rpc_ids(self, make_config):
        cfg = make_config(signal_contacts=["+33600000000"])
        ids = []

        def capture_post(url, json=None, timeout=None):
            ids.append(json["id"])
            r = mock_post.return_value
            r.json.return_value = {"result": {}}
            r.raise_for_status.return_value = None
            return r

        mock_post = patch("alerting.requests.post")
        mock = mock_post.start()
        mock.side_effect = capture_post
        try:
            send_signal(cfg, "body")
            send_signal(cfg, "body")
        finally:
            mock_post.stop()
        assert len(ids) == 2
        assert ids[0] != ids[1]


class TestSendEmail:
    def test_disabled_skips(self, make_config):
        cfg = make_config(email_enabled=False, email_recipients=["a@b.com"])
        assert send_email(cfg, "body", "subj") is False

    def test_no_recipients_skips(self, make_config):
        cfg = make_config(email_enabled=True, email_recipients=[])
        assert send_email(cfg, "body", "subj") is False

    def test_success(self, make_config):
        cfg = make_config(email_enabled=True, email_recipients=["a@b.com"])
        with patch("alerting.smtplib.SMTP") as mock_smtp:
            assert send_email(cfg, "body", "subj") is True
            mock_smtp.assert_called_once()

    def test_smtp_exception_returns_false(self, make_config):
        cfg = make_config(email_enabled=True, email_recipients=["a@b.com"])
        with patch("alerting.smtplib.SMTP", side_effect=Exception("smtp down")):
            assert send_email(cfg, "body", "subj") is False


class TestFireAll:
    def test_all_succeed(self, make_config):
        cfg = make_config()
        with patch("alerting.send_signal", return_value=True), \
             patch("alerting.send_email", return_value=True):
            results = fire_all(cfg, "SOS", "help", None)
        assert results == {"signal": True, "email": True}

    def test_signal_only(self, make_config):
        cfg = make_config()
        with patch("alerting.send_signal", return_value=True), \
             patch("alerting.send_email", return_value=False):
            results = fire_all(cfg, "SOS", "help", None)
        assert results["signal"] is True
        assert results["email"] is False

    def test_all_fail_returns_all_false(self, make_config):
        cfg = make_config()
        with patch("alerting.send_signal", return_value=False), \
             patch("alerting.send_email", return_value=False):
            results = fire_all(cfg, "SOS", "help", None)
        assert results == {"signal": False, "email": False}