import socket

import pytest
import requests

from bellomberg.market_data import filing_pipeline, lettore_trimestrali


def _response(url, status=200, location=None):
    response = requests.Response()
    response.status_code = status
    response.url = url
    response.request = requests.Request("GET", url).prepare()
    response._content = b"documento"
    if location:
        response.headers["Location"] = location
    return response


def _answer(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    return (family, socket.SOCK_STREAM, 6, "", (address, 443))


@pytest.mark.parametrize("host", ["localhost", "a.localhost", "127.0.0.1",
                                        "169.254.169.254", "10.0.0.1", "[::1]"])
def test_private_literal_or_local_name_rejected_before_get(tmp_path, monkeypatch, host):
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append((a, k)))
    url = f"https://{host}/report.pdf"
    result = lettore_trimestrali.scarica_documento(url, str(tmp_path),
                                                   host_consentiti={host.strip('[]')}, public_only=True)
    assert result["stato"] == "errore" and calls == []


def test_mixed_dns_answer_rejected_before_get(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [_answer("8.8.8.8"), _answer("127.0.0.1")])
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append((a, k)))
    result = lettore_trimestrali.scarica_documento("https://ir.example/report.pdf", str(tmp_path),
                                                   host_consentiti={"ir.example"}, public_only=True)
    assert result["stato"] == "errore" and "DNS" in result["motivo"]
    assert calls == []


def test_redirect_is_checked_again_before_next_get(tmp_path, monkeypatch):
    first = "https://ir.example/start"
    second = "https://cdn.example/report.pdf"
    calls = []

    def dns(host, *args, **kwargs):
        return [_answer("8.8.8.8" if host == "ir.example" else "10.0.0.2")]

    def get(url, **kwargs):
        calls.append(url)
        return _response(url, 302, second)

    monkeypatch.setattr(socket, "getaddrinfo", dns)
    monkeypatch.setattr(requests, "get", get)
    result = lettore_trimestrali.scarica_documento(first, str(tmp_path),
                                                   host_consentiti={"ir.example", "cdn.example"},
                                                   public_only=True)
    assert result["stato"] == "errore" and calls == [first]


def test_public_dns_still_downloads_and_i20_requests_strict_mode(tmp_path, monkeypatch):
    url = "https://ir.example/report.pdf"
    calls = []
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [_answer("8.8.8.8")])

    def get(request_url, **kwargs):
        calls.append((request_url, kwargs))
        return _response(request_url)

    monkeypatch.setattr(requests, "get", get)
    result = filing_pipeline._scarica(url, tmp_path, {"ir.example"})
    assert result["stato"] == "ok" and result["bytes"] == len(b"documento")
    assert calls[0][1]["allow_redirects"] is False


def test_dns_failure_is_declared_not_retried_as_unrestricted(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(socket.gaierror("offline")))
    calls = []
    monkeypatch.setattr(requests, "get", lambda *a, **k: calls.append((a, k)))
    result = lettore_trimestrali.scarica_documento("https://ir.example/report.pdf", str(tmp_path),
                                                   host_consentiti={"ir.example"}, public_only=True)
    assert result["stato"] == "errore" and "offline" in result["motivo"] and calls == []
