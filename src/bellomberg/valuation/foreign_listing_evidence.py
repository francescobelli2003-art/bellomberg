"""Reconcile a foreign ordinary listing with filings and its exact quote identity.

Only the explicitly supported narrative grammar and Italian venue are covered.
Unsupported classes/venues stay incomplete; no generic suffix removal or ADR ratio.
"""
from datetime import date
from hashlib import sha256
import calendar
import json
import re

from .statement_table_evidence import _identity

NORMALIZER = 'foreign_ordinary_listing_v1'
PREFIX = 'foreign-listing-'
# Yahoo documents suffixes as additions to the exchange-native ticker:
# https://help.yahoo.com/kb/SLN2310.html (Italian Stock Exchange -> .MI).
PROTOCOL_URL = 'https://help.yahoo.com/kb/SLN2310.html'
VENUE = 'Italian Stock Exchange'
BASIS = 'one_listed_ordinary_share_is_one_share_of_the_same_class'


def _name(value):
    return ''.join(c for c in value.casefold() if c.isalnum()) if isinstance(value, str) else ''


def _proof(source, pattern, role):
    text = ' '.join(source['text'].split())
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1:
        raise ValueError(role + ': unique explicit disclosure unavailable')
    match = matches[0]
    return match, {'role': role, 'document_id': source['id'], 'source_text_sha256': source['sha256'],
        'offset_basis': 'whitespace_collapsed_source_text', 'start': match.start(), 'end': match.end(),
        'text': match.group()}


def normalize_foreign_listing(primary, annual, quote, *, ticker, on, as_of):
    try:
        opening, cutoff = date.fromisoformat(on), date.fromisoformat(as_of)
        pmeta, cik, _ = _identity(primary); ameta, annual_cik, _ = _identity(annual)
        annual_day = date.fromisoformat(ameta['report_date'])
        if (pmeta['form'] not in ('6-K', '20-F') or ameta['form'] != '20-F'
                or pmeta['report_date'] != on or cik != annual_cik
                or _name(pmeta['issuer']) != _name(ameta['issuer'])
                or not annual_day <= opening <= cutoff or (opening-annual_day).days > 366
                or not date.fromisoformat(annual['published_at']) <= date.fromisoformat(primary['published_at']) <= cutoff):
            raise ValueError('foreign filing issuer, form or opening/annual dates disagree')
        label = calendar.month_name[opening.month] + ' ' + str(opening.day) + ', ' + str(opening.year)
        _, capital = _proof(primary,
            r'The Company has an authorized share capital of a single class of [^.]{1,100}'
            r'(?:(?:\.[0-9])[^.]{0,100})? shares having a nominal value of [^.]{1,30}'
            r'(?:\.[0-9]{1,6})? per share\. As of '+re.escape(label)+
            r', there were [0-9][0-9,]* shares issued\.', 'opening_single_class')
        _, ordinary = _proof(annual,
            r'["\u201c]shares["\u201d] refers to ordinary shares, [^.]{1,60}'
            r'(?:\.[0-9]{1,6})?[^.]{0,30} of the Company\.', 'ordinary_class_definition')
        direct, venue = _proof(primary,
            r'The Company[\u2019\x27]s shares trade on (?P<venues>[^.;]{1,180}?)'
            r'(?=,? and (?:its|the Company[\u2019\x27]s) American|;|\.)', 'opening_direct_share_venues')
        if VENUE not in [venue.removeprefix('the ') for venue in direct['venues'].split(' and ')]:
            raise ValueError('supported venue is not a direct ordinary-share listing')
        symbol, listing = _proof(annual,
            r'The shares are also listed on the '+re.escape(VENUE)+
            r' under the symbol ["\u201c](?P<symbol>[A-Z][A-Z0-9]{0,11})["\u201d]\.', 'annual_native_symbol')
        if ticker != symbol['symbol'] + '.MI':
            raise ValueError('exact quote symbol differs from the documented exchange symbol and suffix')
        if quote['sha256'] != sha256(quote['text'].encode()).hexdigest():
            raise ValueError('quote text hash differs')
        body = json.loads(quote['text']); observation = body['observation']
        from .quotation_evidence import historical_quote_document
        rebuilt = historical_quote_document(ticker, on=on, as_of=as_of, fetch=lambda *args: observation)
        if rebuilt['status'] != 'ready':
            raise ValueError('dated unadjusted quotation cannot be verified')
        expected = rebuilt['documents'][0]
        if (body != json.loads(expected['text']) or quote['id'] != expected['id']
                or quote['url'] != expected['url'] or quote['metadata'] != expected['metadata']
                or quote['published_at'] != on):
            raise ValueError('quotation document differs from its observed fields')
        identity = observation['identity']
        observed = date.fromisoformat(observation['identity_observed_on'])
        if (not opening <= observed <= cutoff or identity.get('symbol') != ticker
                or _name(identity.get('longName')) != _name(pmeta['issuer'])
                or identity.get('exchangeName') != 'MIL' or identity.get('fullExchangeName') != 'Milan'
                or identity.get('instrumentType') != 'EQUITY' or identity.get('currency') != 'EUR'
                or observation['currency'] != identity['currency']):
            raise ValueError('observed quote identity, issuer, venue, type, currency or date disagree')
        unit = {'status': 'verified', 'title': 'ordinary shares', 'symbol': ticker,
                'native_symbol': symbol['symbol'], 'exchange': VENUE, 'shares_per_quote': 1, 'basis': BASIS}
        text = json.dumps({'listing': unit, 'proofs': [capital, ordinary, venue, listing],
            'provider_protocol': {'url': PROTOCOL_URL, 'exchange': VENUE, 'suffix': '.MI'},
            'quote_document_id': quote['id'], 'quote_text_sha256': quote['sha256'],
            'identity_observed_on': observed.isoformat(),
            'facts': [{'value': 1, 'unit': 'shares per quote', 'end': on}]},
            ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        doc = {'id': PREFIX+primary['id'], 'url': primary['url'], 'published_at': primary['published_at'],
            'text': text, 'sha256': sha256(text.encode()).hexdigest(), 'document_sha256': primary['id'],
            'origin': NORMALIZER, 'metadata': {'normalizer': NORMALIZER, 'ticker': ticker,
                'source_document_id': primary['id'], 'annual_document_id': annual['id'],
                'quote_document_id': quote['id'], 'share_class': 'ordinary shares', 'basis': BASIS,
                'report_date': on, 'as_of': as_of},
            'extraction_coverage': {'status': 'derived_unit_identity',
                'limitation': 'Single ordinary class and explicit Italian listing only. Provider metadata corroborates identity at acquisition, not a historical share count or ADR ratio.'}}
        return {'status': 'ready', 'documents': [doc], 'issues': []}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        return {'status': 'incomplete', 'documents': [],
                'issues': [{'source': 'foreign ordinary listing', 'reason': str(exc)}]}
