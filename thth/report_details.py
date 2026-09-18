"""Add stable machine-readable reasons without changing legacy prose."""
import functools
import re

RULES = (
    ('未採用の提案', 'study_not_adopted'),
    ('有効母数が不足', 'insufficient_group_samples'),
    ('読めない台帳', 'ledger_incomplete'),
    ('差の原因', 'causality_not_identified'),
    ('24h views が欠測', 'post_views_missing'),
    ('自分の投稿の 24h の刻み', 'post_mark_uncollected'),
    ('24h の刻みが未採取', 'reply_mark_uncollected'),
    ('中央値', 'median_sample_insufficient'),
    ('別 account', 'engagement_other_account'),
    ('帰属不明', 'engagement_account_unknown'),
    ('posted_at が読めず', 'posted_at_unreadable'),
    ('object でなく', 'engagement_malformed'),
    ('topic が文字列でなく', 'topic_invalid'),
    ('根投稿か返信か', 'root_classification_unknown'),
    ('所有 account', 'ownership_unknown'),
    ('読めない実測', 'measured_ledger_unreadable'),
    ('読めない絡み', 'engagement_ledger_unreadable'),
    ('topic shelf が壊れ', 'topic_shelf_unreadable'),
    ('型は投稿時点', 'kind_current_shelf_only'),
    ('現在の型が判らず', 'kind_unknown'),
    ('reply_to / author_key', 'reply_filter_excludes_roots'),
    ('帯ごと', 'hour_band_not_comparable'),
    ('帯が', 'hour_band_not_comparable'),
)

def code(text):
    if re.fullmatch(r'[a-z]+(?:_[a-z]+)*', text):
        return text
    return next((value for fragment, value in RULES if fragment in text), 'report_limitation')

def attach(value):
    if isinstance(value, dict):
        for child in list(value.values()):
            attach(child)
        if isinstance(value.get('cannot_say'), list):
            value['cannot_say_details'] = [{'code': code(text), 'text': text} for text in value['cannot_say']]
    elif isinstance(value, list):
        for child in value:
            attach(child)
    return value

def detailed(function):
    @functools.wraps(function)
    def call(*args, **kwargs):
        return attach(function(*args, **kwargs))
    return call
