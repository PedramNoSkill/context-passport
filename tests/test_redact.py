"""Credential fixtures are dynamically constructed, synthetic, and non-functional."""

import importlib
import unittest


class RedactTests(unittest.TestCase):
    def api(self):
        try:
            return importlib.import_module("context_passport.redact")
        except ModuleNotFoundError:
            self.fail("The redaction module is not implemented")

    def same(self, actual, expected, message="sanitized output differs"):
        # Do not print possibly sensitive text in unittest diagnostics.
        self.assertTrue(actual == expected, message)

    def test_recognizable_provider_credentials_have_safe_counts(self):
        # Shape-only fixtures, assembled to avoid storing credential literals.
        samples = [
            ("github", "gh" + "p_" + "X" * 36),
            ("github", "github_" + "pat_" + "Z" * 82),
            ("openai", "sk" + "-" + "Q" * 48),
            ("openai", "sk" + "-proj-" + "A" * 35 + "_" + "B" * 30),
            ("google", "AI" + "za" + "G" * 35),
            ("aws_access_key", "AK" + "IA" + "Z" * 16),
            ("aws_access_key", "AS" + "IA" + "Y" * 16),
            ("telegram", "123456789" + ":" + "T" * 35),
        ]
        source = "synthetic test credentials: " + " | ".join(s for _, s in samples)
        result = self.api().redact(source)
        expected = "synthetic test credentials: " + " | ".join(
            "[REDACTED:" + kind + "]" for kind, _ in samples
        )
        self.same(result.text, expected)
        self.same(result.counts, {
            "github": 2, "openai": 2, "google": 1, "aws_access_key": 2, "telegram": 1
        })
        self.same(result.total, len(samples))
        for _, secret in samples:
            self.assertTrue(secret not in repr(result), "result metadata leaked a fixture")

    def test_multiline_private_pem_blocks_count_once(self):
        api = self.api()
        for prefix in ("", "RSA ", "DSA ", "EC ", "OPENSSH ", "ENCRYPTED "):
            label = prefix + "PRIVATE " + "KEY"
            secret = "\r\n".join((
                "-----BEGIN " + label + "-----",
                "synthetic-not-key-material",
                "gh" + "p_" + "X" * 36,
                "-----END " + label + "-----",
            ))
            result = api.redact("before\n" + secret + "\nafter")
            self.same(result.text, "before\n[REDACTED:private_key]\nafter")
            self.same(result.counts, {"private_key": 1})
            self.same(result.total, 1)
        public = "-----BEGIN PUBLIC KEY-----\nsynthetic\n-----END PUBLIC KEY-----"
        self.same(api.redact(public).text, public)

    def test_jwt_is_redacted_without_entropy_heuristics(self):
        import base64
        import json

        def segment(value):
            raw = json.dumps(value, separators=(",", ":")).encode("ascii")
            return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

        credential = ".".join((
            segment({"alg": "HS256", "typ": "JWT"}),
            segment({"sub": "synthetic-non-functional"}),
            segment({"signature": "invalid-for-testing"}),
        ))
        ordinary = "sha256 " + "c7" * 32 + " file/version.2026.11 ordinary text"
        result = self.api().redact(ordinary + "\n" + credential + ".")
        self.same(result.text, ordinary + "\n[REDACTED:jwt].")
        self.same(result.counts, {"jwt": 1})

    def test_authorization_headers_redact_outer_span_once(self):
        credential = "gh" + "p_" + "X" * 36
        examples = [
            ("Authorization: Bearer " + credential, "Authorization: [REDACTED:authorization]"),
            ("proxy-authorization: Basic " + "U" * 24, "proxy-authorization: [REDACTED:authorization]"),
            ('{"Authorization": "Bearer ' + credential + '"}',
             '{"Authorization": "[REDACTED:authorization]"}'),
            ("curl -H 'Authorization: token " + credential + "' https://example.invalid",
             "curl -H 'Authorization: [REDACTED:authorization]' https://example.invalid"),
        ]
        for source, expected in examples:
            result = self.api().redact(source)
            self.same(result.text, expected)
            self.same(result.counts, {"authorization": 1})
            second = self.api().redact(result.text)
            self.same(second.text, result.text)
            self.same(second.total, 0)

    def test_url_userinfo_is_redacted_with_safe_destination_preserved(self):
        credential = "gh" + "p_" + "X" * 36
        urls = [
            "https://synthetic-user:synthetic-pass@example.invalid/path?q=1",
            "postgresql://synthetic-user:synthetic-pass@db.invalid:5432/example",
            "mongodb+srv://synthetic-user:" + credential + "@db.invalid/example",
            "redis://:synthetic-pass@cache.invalid:6379/0",
            "https://synthetic%40user:synthetic%3Apass@example.invalid/",
        ]
        expected = [
            "https://[REDACTED:url_credentials]@example.invalid/path?q=1",
            "postgresql://[REDACTED:url_credentials]@db.invalid:5432/example",
            "mongodb+srv://[REDACTED:url_credentials]@db.invalid/example",
            "redis://[REDACTED:url_credentials]@cache.invalid:6379/0",
            "https://[REDACTED:url_credentials]@example.invalid/",
        ]
        result = self.api().redact("\n".join(urls))
        self.same(result.text, "\n".join(expected))
        self.same(result.counts, {"url_credentials": len(urls)})
        second = self.api().redact(result.text)
        self.same(second.text, result.text)
        self.same(second.total, 0)

    def test_explicit_secret_assignments_preserve_structure(self):
        credential = "sk" + "-proj-" + "X" * 40
        source = "\n".join((
            'password = "synthetic pass phrase"',
            "TOKEN='synthetic-only-token'",
            '{"api_key": "' + credential + '", "ok": true}',
            "export CLIENT_SECRET=synthetic-client-secret # keep comment",
            "aws_secret_access_key: synthetic-aws-secret",
            "Password=synthetic-db-password;Server=db.invalid;Port=5432",
            "token=synthetic-query&other=retained",
            r'{"password": "synthetic\"quoted\\value", "normal": "ok"}',
        ))
        expected = "\n".join((
            'password = "[REDACTED:secret]"',
            "TOKEN='[REDACTED:secret]'",
            '{"api_key": "[REDACTED:secret]", "ok": true}',
            "export CLIENT_SECRET=[REDACTED:secret] # keep comment",
            "aws_secret_access_key: [REDACTED:secret]",
            "Password=[REDACTED:secret];Server=db.invalid;Port=5432",
            "token=[REDACTED:secret]&other=retained",
            '{"password": "[REDACTED:secret]", "normal": "ok"}',
        ))
        result = self.api().redact(source)
        self.same(result.text, expected)
        self.same(result.counts, {"secret": 8})
        second = self.api().redact(result.text)
        self.same(second.text, result.text)
        self.same(second.total, 0)

    def test_private_key_suffix_assignments_are_sensitive(self):
        names = ["private_key", "SSH_PRIVATE_KEY", "signing-private-key", "servicePrivateKey"]
        source = "\n".join(name + '="synthetic key material"' for name in names)
        result = self.api().redact(source)
        expected = "\n".join(name + '="[REDACTED:secret]"' for name in names)
        self.same(result.text, expected)
        self.same(result.counts, {"secret": len(names)})

    def test_nested_json_is_copied_with_sensitive_values_replaced(self):
        import copy
        credential = "gh" + "p_" + "X" * 36
        source = {
            "name": "synthetic fixture",
            "items": [
                {"password": "synthetic-low-entropy", "enabled": True, "attempts": 3},
                {"API_KEY": credential, "TOKEN": 123456},
                [None, False, 1.25, credential],
            ],
            "empty": {"password": "", "token": None},
        }
        original = copy.deepcopy(source)
        api = self.api()
        self.assertTrue(hasattr(api, "redact_value"), "recursive API is missing")
        sanitized, counts = api.redact_value(source)
        expected = {
            "name": "synthetic fixture",
            "items": [
                {"password": "[REDACTED:secret]", "enabled": True, "attempts": 3},
                {"API_KEY": "[REDACTED:secret]", "TOKEN": "[REDACTED:secret]"},
                [None, False, 1.25, "[REDACTED:github]"],
            ],
            "empty": {"password": "", "token": None},
        }
        self.same(sanitized, expected)
        self.same(counts, {"secret": 3, "github": 1})
        self.same(source, original, "input was mutated")
        self.assertIsNot(sanitized, source)
        self.assertIsNot(sanitized["items"], source["items"])
        self.assertIsNot(sanitized["items"][0], source["items"][0])
        again, second_counts = api.redact_value(sanitized)
        self.same(again, sanitized)
        self.same(second_counts, {})

    def test_sensitive_containers_propagate_to_all_leaf_values(self):
        source = {
            "servicePrivateKey": {"material": ["synthetic-one", "synthetic-two"], "format": "PEM"},
            "client_secret": [{"value": "synthetic-three"}, 0, False],
            "authorization": "Basic synthetic-invalid-encoding",
            "normal": {"name": "preserved"},
        }
        sanitized, counts = self.api().redact_value(source)
        self.same(sanitized, {
            "servicePrivateKey": {"material": ["[REDACTED:secret]", "[REDACTED:secret]"],
                                  "format": "[REDACTED:secret]"},
            "client_secret": [{"value": "[REDACTED:secret]"}, "[REDACTED:secret]", "[REDACTED:secret]"],
            "authorization": "[REDACTED:secret]",
            "normal": {"name": "preserved"},
        })
        self.same(counts, {"secret": 7})

    def test_secret_dict_keys_are_sanitized_without_collision_data_loss(self):
        first = "gh" + "p_" + "X" * 36
        second = "gh" + "p_" + "Y" * 36
        source = {
            "prefix " + first: {"data": "preserved"},
            first: "one",
            second: "two",
            "[REDACTED:github]": "preexisting placeholder key",
        }
        sanitized, counts = self.api().redact_value(source)
        self.same(len(sanitized), len(source), "sanitized key collision lost data")
        self.same(list(sanitized.values()), list(source.values()))
        self.assertTrue("prefix [REDACTED:github]" in sanitized, "secret key was retained")
        self.same(counts, {"github": 3})
        self.assertTrue(first not in repr(sanitized), "first key fixture leaked")
        self.assertTrue(second not in repr(sanitized), "second key fixture leaked")
        again, second_counts = self.api().redact_value(sanitized)
        self.same(again, sanitized)
        self.same(second_counts, {})

    def test_existing_markers_do_not_shield_adjacent_secrets(self):
        source = 'password="[REDACTED:secret] synthetic-unredacted-tail"'
        result = self.api().redact(source)
        self.same(result.text, 'password="[REDACTED:secret]"')
        self.same(result.counts, {"secret": 1})
        value = {"password": "prefix [REDACTED:secret] suffix"}
        sanitized, counts = self.api().redact_value(value)
        self.same(sanitized, {"password": "[REDACTED:secret]"})
        self.same(counts, {"secret": 1})

    def test_multifield_authorization_headers_redact_the_complete_value(self):
        source = (
            'Authorization: Digest username="synthetic-user", realm="example.invalid", '
            'nonce="synthetic-nonce", response="synthetic-response"\n'
            'X-Request-ID: retained\n'
            'Authorization: synthetic-custom-credential'
        )
        result = self.api().redact(source)
        self.same(result.text, (
            "Authorization: [REDACTED:authorization]\n"
            "X-Request-ID: retained\n"
            "Authorization: [REDACTED:authorization]"
        ))
        self.same(result.counts, {"authorization": 2})

    def test_partial_overlaps_are_merged_so_the_union_cannot_leak(self):
        source = "https://synthetic-user:password=synthetic-value@example.invalid/path"
        result = self.api().redact(source)
        self.same(result.text, "https://[REDACTED:url_credentials]")
        self.same(result.counts, {"url_credentials": 1})
        again = self.api().redact(result.text)
        self.same(again.text, result.text)
        self.same(again.total, 0)

    def test_json_assignments_allow_multiline_whitespace(self):
        source = '{\n  "client_secret"\n  :\n  "synthetic-multiline-json"\n}'
        result = self.api().redact(source)
        self.same(result.text, '{\n  "client_secret"\n  :\n  "[REDACTED:secret]"\n}')
        self.same(result.counts, {"secret": 1})
        empty_environment_value = "PASSWORD=\nBuild succeeded. No API key needed."
        self.same(self.api().redact(empty_environment_value).text, empty_environment_value)

    def test_invalid_inputs_raise_generic_errors_without_echoing_values(self):
        api = self.api()
        credential = "gh" + "p_" + "X" * 36
        class Unsupported:
            def __repr__(self):
                return credential

        cases = [
            (api.redact, Unsupported()),
            (api.redact_value, Unsupported()),
            (api.redact_value, {Unsupported(): "synthetic"}),
            (api.redact_value, {credential: {"value": Unsupported()}}),
        ]
        for operation, value in cases:
            caught = None
            try:
                operation(value)
            except (TypeError, ValueError) as error:
                caught = error
            self.assertTrue(caught is not None, "unsupported JSON input was accepted")
            self.assertTrue(credential not in str(caught), "exception leaked a fixture")

    def test_truncated_pem_removes_material_through_end_of_input(self):
        label = "OPENSSH PRIVATE " + "KEY"
        source = "safe preface\n-----BEGIN " + label + "-----\nsynthetic-key-material\ncontinued"
        result = self.api().redact(source)
        self.same(result.text, "safe preface\n[REDACTED:private_key]")
        self.same(result.counts, {"private_key": 1})

    def test_json_authorization_values_support_custom_and_multifield_schemes(self):
        import json
        source = json.dumps({
            "Authorization": 'Digest username="synthetic", response="synthetic-response"',
            "Proxy-Authorization": "SyntheticCustom synthetic-invalid-token",
            "normal": "preserved",
        })
        result = self.api().redact(source)
        self.same(json.loads(result.text), {
            "Authorization": "[REDACTED:authorization]",
            "Proxy-Authorization": "[REDACTED:authorization]",
            "normal": "preserved",
        })
        self.same(result.counts, {"authorization": 2})

    def test_telegram_api_url_bot_prefix_does_not_hide_token(self):
        credential = "123456789" + ":" + "T" * 35
        source = "https://api.telegram.org/bot" + credential + "/getMe"
        result = self.api().redact(source)
        self.same(result.text, "https://api.telegram.org/bot[REDACTED:telegram]/getMe")
        self.same(result.counts, {"telegram": 1})

    def test_unquoted_yaml_secret_lines_redact_the_full_passphrase(self):
        source = (
            "database:\n"
            "  password: synthetic multi word passphrase # retained comment\n"
            "  user: retained\n"
            "API key: synthetic multi word key\n"
            "No API key needed.\n"
        )
        result = self.api().redact(source)
        self.same(result.text, (
            "database:\n"
            "  password: [REDACTED:secret] # retained comment\n"
            "  user: retained\n"
            "API key: [REDACTED:secret]\n"
            "No API key needed.\n"
        ))
        self.same(result.counts, {"secret": 2})
        second = self.api().redact(result.text)
        self.same(second.text, result.text)
        self.same(second.total, 0)

    def test_plain_text_returns_result_with_empty_counts(self):
        api = self.api()
        text = "No API key needed. Document version 2026. Build succeeded."
        result = api.redact(text)
        self.assertIsInstance(result, api.RedactionResult)
        self.same(result.text, text)
        self.same(result.counts, {})
        self.same(result.total, 0)
        self.same(api.redact("").text, "")


if __name__ == "__main__":
    unittest.main()
