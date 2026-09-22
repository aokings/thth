"""One static table of what each medium can carry, and why not when it cannot.

**なぜ表が要るか**（設計 2.13.0 §0.1）。「この媒体に動画は出せるか」を LLM が
推測で答えると、出せないものを原稿に書き、lint で断られてから初めて判る。表は
**道具に聞ける形**で同じことを先に言う——`thth doctor`・`handoff-report` の
`tool.capabilities`・`docs/原稿_添付_2.13.md` の 3 か所は、この 1 本から出る。

## 値の語彙（固定）

- `supported` — thth が出せる。fake/record-replay の試験が通っている。
- `unsupported: <理由>` — 出せない。理由は静的な語（値・パス・URL を含まない）。
- `unverified: <理由>` — **出せないとは言えないが、確かめてもいない。**
  「未確認」を「非対応」と書かない（設計 §0.1）。
- `ascii_only: <理由>` — ASCII の範囲でだけ出せる。
- `body_only` — 専用の口は無く、本文に書いたものがそのまま扱われる。

`supported` は「thth が API に渡せる」ことであって、**実機で masaru の
account に出た**という意味ではない。実機の測定が要る行は検収依頼に並ぶ。

## 表と理由コードの関係

lint と adapter が投げる `unsupported_attachment: <媒体>/<key>` の `<key>` は
**この表の key でなければならない**（依頼 第 10 段）。種類・形式を名指しする
key は `CAPABILITIES` に、組合せ・形の不正を名指しする key は `REFUSALS` に
置く。`keys(medium)` はその和で、`tests/test_v213_capabilities.py` が
`thth/` の source から拾った理由 key の集合がこれの部分集合であることを見る
——**表から key を消すと、その理由コードを投げている実装が試験で落ちる。**
`CAPABILITIES` だけが外（doctor・tool・docs）に出る。
"""
from __future__ import annotations

MEDIA = ('mastodon', 'bluesky', 'threads', 'x')

# 語彙。`body_only` だけ理由を取らない（「本文に書く」が理由そのもの）。
PREFIXES = ('unsupported', 'unverified', 'ascii_only')
VOCABULARY = ('supported', 'body_only') + tuple(p + ': ' for p in PREFIXES)

# 静的な理由（同じことは同じ語で言う）。
NO_AUDIO = 'unsupported: provider_has_no_audio_attachment'
NO_CAPTION = 'unsupported: provider_has_no_caption_upload'
NO_THUMBNAIL = 'unsupported: provider_has_no_thumbnail_upload'
NO_TEXT = 'unsupported: provider_has_no_text_attachment'
NO_LABEL = 'unsupported: provider_has_no_label_field'
NO_LANGUAGE = 'unsupported: provider_has_no_language_field'
NO_SPOILER = 'unsupported: provider_has_no_spoiler_field'
NO_REPLY_CONTROL = 'unsupported: provider_has_no_reply_control'
NO_GHOST = 'unsupported: provider_has_no_ghost_post'
NO_POLL = 'unsupported: provider_has_no_poll'
NO_TAG = 'unsupported: provider_has_no_tag_field'
NO_OFFSETS = 'unsupported: provider_has_no_rich_text_offsets'
PROVIDER_FORMAT = 'unsupported: provider_format'
PRIVATE = 'unsupported: ledger_public_only'
ADTS = 'unsupported: privacy_inspection_boundary_unverified'
ASF = 'unsupported: deferred_by_ruling_c14'
BMFF_BRAND = 'unsupported: unrecognized_bmff_brand'
UNKNOWN_FORMAT = 'unsupported: unrecognized_container'
NOT_MEASURED = 'unverified: provider_not_measured'
# 引用は「API に無い」のではなく、**自己サーブの tier では使えない**（2.14 §7 の
# 一次資料: "Quote-posting … requires an Enterprise plan"）。理由を tier に名指す。
TIER_ONLY = 'unsupported: provider_tier_enterprise_only'

CAPABILITIES = {
    'mastodon': {
        # 種類
        'image': 'supported', 'video': 'supported', 'audio': 'supported',
        'carousel': 'supported', 'poll': 'supported', 'quote': 'supported',
        'link': 'body_only', 'gif': 'supported', 'text': NO_TEXT,
        'thumbnail': 'supported', 'captions': NO_CAPTION,
        # 投稿の付加情報
        'tags': 'body_only', 'labels': NO_LABEL, 'languages': 'supported',
        'spoiler': 'supported', 'reply_control': NO_REPLY_CONTROL, 'ghost': NO_GHOST,
        'visibility_private': PRIVATE, 'offset_styling': NO_OFFSETS,
        'carousel_quote': NOT_MEASURED,
        # 形式（magic bytes で判定した結果の名前）
        'jpeg': 'supported', 'png': 'supported', 'webp': 'supported',
        'mp4': 'supported', 'mov': 'supported', 'webm': 'supported',
        'mp3': 'supported', 'wav': 'supported', 'flac': 'supported',
        'ogg': 'supported', 'ogg_vorbis': 'supported', 'vtt': NO_CAPTION,
        'aac_adts': ADTS, 'asf': ASF,
        'bmff brand': BMFF_BRAND, 'unknown format': UNKNOWN_FORMAT,
    },
    'bluesky': {
        'image': 'supported', 'video': 'supported', 'audio': NO_AUDIO,
        'carousel': 'supported', 'poll': NO_POLL, 'quote': 'supported',
        'link': 'supported', 'gif': 'supported', 'text': NO_TEXT,
        'thumbnail': 'supported', 'captions': 'supported',
        'tags': 'supported', 'labels': 'supported', 'languages': 'supported',
        'spoiler': NO_SPOILER, 'reply_control': 'unsupported: threadgate_not_implemented',
        'ghost': NO_GHOST, 'visibility_private': PRIVATE,
        'offset_styling': 'supported', 'carousel_quote': 'supported',
        'jpeg': 'supported', 'png': 'supported', 'webp': 'supported',
        'mp4': 'supported', 'mov': PROVIDER_FORMAT, 'webm': PROVIDER_FORMAT,
        'mp3': NO_AUDIO, 'wav': NO_AUDIO, 'flac': NO_AUDIO,
        'ogg': NO_AUDIO, 'ogg_vorbis': NO_AUDIO, 'vtt': 'supported',
        'aac_adts': ADTS, 'asf': ASF,
        'bmff brand': BMFF_BRAND, 'unknown format': UNKNOWN_FORMAT,
    },
    'threads': {
        'image': 'supported', 'video': 'supported', 'audio': NO_AUDIO,
        'carousel': 'supported', 'poll': 'supported', 'quote': 'supported',
        'link': 'supported', 'gif': 'supported', 'text': 'supported',
        'thumbnail': NO_THUMBNAIL, 'captions': NO_CAPTION,
        'tags': 'supported', 'labels': NO_LABEL, 'languages': NO_LANGUAGE,
        'spoiler': 'supported', 'reply_control': 'supported', 'ghost': 'supported',
        'visibility_private': PRIVATE,
        'offset_styling': 'ascii_only: threads_offset_unit_unverified',
        'carousel_quote': NOT_MEASURED,
        'jpeg': 'supported', 'png': 'supported', 'webp': PROVIDER_FORMAT,
        'mp4': 'supported', 'mov': 'supported', 'webm': PROVIDER_FORMAT,
        'mp3': NO_AUDIO, 'wav': NO_AUDIO, 'flac': NO_AUDIO,
        'ogg': NO_AUDIO, 'ogg_vorbis': NO_AUDIO, 'vtt': NO_CAPTION,
        'aac_adts': ADTS, 'asf': ASF,
        'bmff brand': BMFF_BRAND, 'unknown format': UNKNOWN_FORMAT,
    },
    'x': {
        'image': 'supported', 'video': 'supported', 'audio': NO_AUDIO,
        'carousel': 'supported', 'poll': 'supported', 'quote': TIER_ONLY,
        'link': 'body_only', 'gif': 'supported', 'text': NO_TEXT,
        'thumbnail': NO_THUMBNAIL, 'captions': NO_CAPTION,
        'tags': 'body_only', 'labels': NO_LABEL, 'languages': NO_LANGUAGE,
        'spoiler': NO_SPOILER, 'reply_control': 'supported', 'ghost': NO_GHOST,
        'visibility_private': PRIVATE, 'offset_styling': PROVIDER_FORMAT,
        'carousel_quote': TIER_ONLY,
        'jpeg': 'supported', 'png': 'supported', 'webp': PROVIDER_FORMAT,
        'mp4': 'supported', 'mov': PROVIDER_FORMAT, 'webm': PROVIDER_FORMAT,
        'mp3': NO_AUDIO, 'wav': NO_AUDIO, 'flac': NO_AUDIO,
        'ogg': NO_AUDIO, 'ogg_vorbis': NO_AUDIO, 'vtt': NO_CAPTION,
        'aac_adts': ADTS, 'asf': ASF,
        'bmff brand': BMFF_BRAND, 'unknown format': UNKNOWN_FORMAT,
    },
}

# 一語の `supported` が嘘にならないための但し書き。値ではなく静的な語。
NOTES = {
    'mastodon': {
        'gif': 'gif_file_only_typed_giphy_attachment_is_threads_only',
        'quote': 'requires_instance_api_version_7',
        'languages': 'single_language_option',
        'tags': 'body_hashtags_observed_by_the_provider',
        'carousel': 'instance_limit_applies',
    },
    'bluesky': {
        'gif': 'gif_file_only_no_typed_gif_attachment',
        'link': 'external_card_with_optional_thumbnail',
        'captions': 'vtt_on_the_first_video_only',
        'carousel': 'images_or_gallery_one_kind_per_post',
        'offset_styling': 'facets_use_utf8_byte_offsets',
    },
    'threads': {
        'gif': 'giphy_provider_only_no_gif_file_upload',
        'tags': 'one_topic_tag_per_post',
        'carousel': 'images_and_video_may_be_mixed',
        'text': 'long_text_attachment_up_to_10000_characters',
    },
    'x': {
        'image': 'up_to_four_images_per_post',
        'gif': 'gif_file_upload_one_per_post_not_mixed_with_images',
        'video': 'one_video_per_post_not_mixed_with_images',
        'carousel': 'images_only_one_kind_per_post',
        'poll': 'two_to_four_options_duration_minutes_five_to_10080',
        'reply_control': 'reply_settings_following_mentionedusers_subscribers_verified',
        'tags': 'body_hashtags_observed_by_the_provider',
        'link': 'provider_generates_the_card_from_the_body_url',
        'mov': 'thth_sends_mp4_only_in_this_version',
    },
}

# 組合せ・形の不正だけを名指しする理由 key（表には出さない・理由は静的）。
REFUSALS = {
    'mastodon': {
        'typed_attachment_pending': 'typed attachment kind not routed for this medium',
        'poll_media_exclusive': 'poll and files cannot share one status',
        'custom_card_fields': 'link card fields are generated by the instance',
        'captions': 'caption file with a status',
        'poll_focus': 'focus point without an image',
        'fileless_focus': 'focus point without a file',
        'post_options': 'post option outside the mastodon set',
        'non_public_visibility': 'visibility outside public/unlisted',
        'no_media': 'media intent with nothing to send',
        'thumbnail_parent': 'thumbnail without an audio/video parent',
        'format': 'inspected format outside the mastodon set',
        'quote_requires_api_7': 'instance api version below 7',
    },
    'bluesky': {
        'typed_attachment_pending': 'typed attachment kind not routed for this medium',
        'external_post_options': 'post option outside the external-card set',
        'external_thumbnail': 'external card thumbnail must be one image',
        'image_captions': 'caption file with images',
        'image_post_options': 'post option outside the image set',
        'video_post_options': 'post option outside the video set',
        'no_images': 'image intent with no image',
        'caption': 'caption must be vtt on the first video',
    },
    'x': {
        'typed_attachment': 'typed attachment kind not routed for this medium',
        'poll_media_exclusive': 'poll and files cannot share one post',
        'poll_option': 'poll field outside {type,options,duration_minutes}',
        'post_options': 'post option outside the x set',
        'reply_settings': 'reply_settings outside the provider enum',
        'media_mixed_kinds': 'images cannot share a post with a gif or a video',
        'no_media': 'media intent with nothing to send',
        'captions': 'caption file with a post',
        'format': 'inspected format outside the x set',
    },
    'threads': {
        'typed_attachment': 'typed attachment kind not routed for this medium',
        'link_requires_text': 'link preview cannot accompany files',
        'link_option': 'link field outside {type,url}',
        'quote_option': 'quote field outside {type,uri}',
        'poll_option': 'poll field outside {type,options}',
        'poll_combination': 'poll with an attachment other than a quote',
        'gif_requires_text': 'gif cannot accompany files',
        'gif_option': 'gif field outside {type,provider,id}',
        'gif_provider': 'gif provider other than GIPHY',
        'image_post_options': 'post option outside the threads set',
        'media_spoiler_requires_media': 'media spoiler without media',
        'ghost_requires_text': 'ghost post cannot carry files',
        'ghost_combination': 'ghost post with another attachment or option',
        'ghost_reply': 'ghost post as a reply',
    },
}


def valid(status):
    """語彙のとおりか（`unsupported`/`unverified`/`ascii_only` は理由が要る）。"""
    if not isinstance(status, str):
        return False
    if status in ('supported', 'body_only'):
        return True
    for prefix in PREFIXES:
        if status.startswith(prefix + ': '):
            reason = status[len(prefix) + 2:]
            return bool(reason.strip()) and reason == reason.strip()
    return False


def table(medium=None):
    """外に出す表。`medium` を渡せばその 1 行だけ（読む側の copy）。"""
    if medium is None:
        return {name: dict(row) for name, row in CAPABILITIES.items()}
    return dict(CAPABILITIES.get(medium, {}))


def notes(medium=None):
    if medium is None:
        return {name: dict(row) for name, row in NOTES.items()}
    return dict(NOTES.get(medium, {}))


def keys(medium):
    """理由コードに使ってよい key の全部（表 ∪ 組合せの理由）。"""
    return frozenset(CAPABILITIES.get(medium, {})) | frozenset(REFUSALS.get(medium, {}))


def summary():
    """`tool.capabilities`。表と但し書きと語彙を 1 つの静的な payload に。"""
    return {'schema_version': 1, 'vocabulary': list(VOCABULARY),
            'basis': 'static_table_not_a_provider_probe',
            'media': table(), 'notes': notes(),
            'read_before_attaching': '添付を付ける前に tool.capabilities を読む'}


def lines(medium):
    """`thth doctor` の本文（1 行 1 key・値も但し書きも静的）。"""
    row = CAPABILITIES.get(medium)
    if not row:
        return ['添付の対応表: この媒体の行はありません（media_capabilities_unknown_medium）']
    out = ['添付の対応表（' + medium + '・静的。実機の測定ではありません）:']
    note = NOTES.get(medium, {})
    for key in sorted(row):
        suffix = '（' + note[key] + '）' if key in note else ''
        out.append('  ' + key + ': ' + row[key] + suffix)
    return out
