from stalled_news.whitelist import WhitelistPolicy, host_from_url, is_url_allowed


def policy():
    return WhitelistPolicy.from_config(
        allowed_domains=[
            "economictimes.indiatimes.com",
            "thehindu.com",
            "gov.in",
        ],
        subdomain_allowed=[
            "gov.in",
        ],
    )


def test_host_from_url_basic():
    assert host_from_url("https://thehindu.com/a/b") == "thehindu.com"
    assert host_from_url("http://www.thehindu.com") == "thehindu.com"
    assert host_from_url("thehindu.com/news") == "thehindu.com"


def test_exact_domain_allowed():
    p = policy()
    assert is_url_allowed("https://economictimes.indiatimes.com/xyz", p) is True
    assert is_url_allowed("https://indiatimes.com/xyz", p) is False


def test_subdomain_allowed_only_when_configured():
    p = policy()
    assert is_url_allowed("https://foo.gov.in/notice", p) is True
    assert is_url_allowed("https://bar.thehindu.com/x", p) is False  # no subdomains for thehindu.com


def test_reject_unknown():
    p = policy()
    assert is_url_allowed("https://randomsite.com/a", p) is False


def test_handles_ports_and_www():
    p = policy()
    assert is_url_allowed("https://www.thehindu.com:443/a", p) is True
