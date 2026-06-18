from matching import contains_keyword, first_matching_keyword


class TestContainsKeyword:
    def test_basic_match(self):
        assert contains_keyword("I need HELP now", "HELP") is True

    def test_case_insensitive(self):
        assert contains_keyword("i need help now", "help") is True

    def test_standalone(self):
        assert contains_keyword("SOS", "SOS") is True

    def test_punctuation_boundary(self):
        assert contains_keyword("HELP!", "HELP") is True
        assert contains_keyword("SOS.", "SOS") is True
        assert contains_keyword("(SOS)", "SOS") is True

    def test_no_substring_match(self):
        # The whole reason this module exists: short keywords must not match
        # as substrings of larger words.
        assert contains_keyword("sossisson", "sos") is False   # "sausage"
        assert contains_keyword("broken", "OK") is False
        assert contains_keyword("book", "OK") is False
        assert contains_keyword("coke", "OK") is False
        assert contains_keyword("helping", "HELP") is False
        assert contains_keyword("helmet", "HELP") is False
        assert contains_keyword("maydays", "MAYDAY") is False

    def test_no_match(self):
        assert contains_keyword("hello world", "SOS") is False

    def test_empty_text(self):
        assert contains_keyword("", "SOS") is False

    def test_empty_keyword(self):
        # An empty keyword matches at every word boundary; assert it is False
        # so we don't silently allow a misconfigured empty keyword to trigger.
        assert contains_keyword("anything", "") is False

    def test_regex_metachar_keyword_is_escaped(self):
        assert contains_keyword("FA-OK done", "FA-OK") is True
        assert contains_keyword("FA OK", "FA-OK") is False


class TestFirstMatchingKeyword:
    def test_returns_first_match(self):
        kws = ["HELP", "SOS", "MAYDAY"]
        assert first_matching_keyword("SOS sent", kws) == "SOS"

    def test_none_when_no_match(self):
        assert first_matching_keyword("all good", ["HELP", "SOS"]) is None

    def test_first_in_list_priority(self):
        # When two keywords match, the one declared first wins.
        assert first_matching_keyword("SOS and HELP", ["SOS", "HELP"]) == "SOS"
        assert first_matching_keyword("SOS and HELP", ["HELP", "SOS"]) == "HELP"

    def test_word_boundary_only(self):
        assert first_matching_keyword("sossisson", ["sos"]) is None
        assert first_matching_keyword("real SOS here", ["sos"]) == "sos"