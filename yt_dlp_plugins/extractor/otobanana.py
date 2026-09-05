import urllib.parse

from yt_dlp.extractor.common import InfoExtractor
from yt_dlp.utils import (
    determine_ext,
    traverse_obj,
    parse_iso8601,
    parse_duration,
    int_or_none,
    str_or_none,
    url_or_none,
)


class OtobananaBaseIE(InfoExtractor):
    _VALID_URL = False
    _CATEGORIES = {
        # https://api.v2.otobanana.com/api/casts/categories?is_adult=false
        "9ca29dd1-a025-4dd6-84a0-54972c5fb099": ("talk", "雑談"),
        "9ca29ec0-c6ff-4997-9373-b4f25fc78d63": ("read", "読み聞かせ"),
        "9ca29f26-1ca0-4c01-a4a8-c461dfd5e414": ("asmr", "ASMR"),
        "9ca29f4e-034e-43dc-904a-982aae9a5a2e": ("hobby", "趣味"),
        "9ca29f62-173e-4d56-bdab-487a899d9bbe": ("other", "その他"),
        # https://api.v2.otobanana.com/api/casts/categories?is_adult=true
        "9ca29fbe-95f2-43f9-867d-908da75c6646": ("for_men", "男性向け"),
        "9ca29fd7-f747-44d5-85af-7fa3cfd398dd": ("for_women", "女性向け"),
        "9ca29fef-3823-4c02-a6e7-0fc5fced5b8c": ("bl", "ボーイズラブ"),
        "9ca2a01f-4faa-4aef-a17f-4ece6ff95de1": ("liliy", "百合・ガールズラブ"),
        "9ca2a034-1863-4d29-bbc8-eddf179cc058": ("maniac", "マニアック"),
        "9d864222-684f-4b3a-bbd5-4a1e28fef7b3": ("official", "公式"),
        "9ca2a052-bc57-410a-ba07-98de8c6d0308": ("other", "その他"),
    }

    def _parse_cast_data(self, data, floor, cast_id=None):
        post = traverse_obj(data, "post")
        user = traverse_obj(post, "user")
        category_id = traverse_obj(data, ("category_id", {str_or_none}))
        category_key, category_name = self._CATEGORIES.get(category_id, (None, None))

        return {
            "id": cast_id,
            "categories": [category_name] if category_name else None,
            "category_key": category_key, # site-specific
            **traverse_obj(data, {
                "formats": ("audio_url", {
                    lambda x: ([ { "url": x, "format_id": "direct", "ext": determine_ext(x) } ] if x else None)
                }),
                "duration": ("duration_time", {parse_duration}),
                "thumbnail": (("thumbnail_url", ("post", "user", "avatar_url")), any, {url_or_none}),
                "age_limit": ("is_adult", {lambda x: 18 if x else None}),
            }),
            **traverse_obj(post, {
                "title": ("title", {str_or_none}),
                "alt_title": ("mask_title", {str_or_none}),
                "description": ("text", {str_or_none}),
                "timestamp": ("created_at", {parse_iso8601}),
                "view_count": ("play_count", {int_or_none}),
                "like_count": ("like_count", {int_or_none}),
                "comment_count": ("comment_count", {int_or_none}),
                "availability": ("restriction", {
                    lambda x: None if x is None else ("public" if x < 3 else "subscriber_only")
                }),
                # site-specific
                "mask_title": ("mask_title", {str_or_none}),
                "text": ("text", {str_or_none}),
                "mask_text": ("mask_text", {str_or_none}),
                "play_count": ("play_count", {int_or_none}),
                "gift_banana": ("gift_banana", {int_or_none}),
            }),
            **traverse_obj(user, {
                "uploader": ("name", {str_or_none}),
                "uploader_id": ("id", {str_or_none}),
                "uploader_url": ("id", {str_or_none}, {
                    lambda x: (f"https://otobanana.com/{floor}/user/{x}" if x else None)
                }),
                # site-specific
                "bio": ("bio", {str_or_none}),
                "mask_bio": ("mask_bio", {str_or_none}),
                "mask_name": ("mask_name", {str_or_none}),
                "username": ("username", {str_or_none}),
                "twitter_username": ("twitter_username", {str_or_none}),
                "gender": ("gender", {int_or_none}),
                "gender_label": ("gender_label", {str_or_none}),
                "followee_count": ("followee_count", {int_or_none}),
                "follower_count": ("follower_count", {int_or_none}),
                "cien_url": ("cien_url", {url_or_none}),
                "dlsite_url": ("dlsite_url", {url_or_none}),
            }),
        }


class OtobananaIE(OtobananaBaseIE):
    _VALID_URL = r"https?://otobanana\.com/(?P<floor>general|deep)/cast/(?P<id>[^/?#]+)"

    def _real_extract(self, url):
        floor, cast_id = self._match_valid_url(url).group("floor", "id")
        data = self._download_json(
            f"https://api.v2.otobanana.com/api/casts/{cast_id}", cast_id)
        return self._parse_cast_data(data, floor, cast_id)


class OtobananaUserCastIE(OtobananaBaseIE):
    _VALID_URL = r"https?://otobanana\.com/(?P<floor>general|deep)/user/(?P<id>[^/?#]+)"

    def _entries(self, next_page_url, floor, user_id):
        req_num = 1
        while next_page_url:
            res_data = self._download_json(
                next_page_url, user_id, note=f"Downloading JSON metadata {req_num}")
            for entry in res_data.get("data", []):
                cast_id = traverse_obj(entry, ("post_ptr_id"))
                if not cast_id:
                    continue
                info = self._parse_cast_data(entry, floor, cast_id)
                info["webpage_url"] = f"https://otobanana.com/{floor}/cast/{cast_id}"
                info["extractor_key"] = OtobananaIE.ie_key()
                info["extractor"] = OtobananaIE.IE_NAME
                info["original_url"] = info["webpage_url"] 
                yield info
            next_page_url = url_or_none(res_data.get("next_page_url"))
            req_num += 1

    def _real_extract(self, url):
        floor, user_id = self._match_valid_url(url).group("floor", "id")
        user_info = self._download_json(
            f"https://api.v2.otobanana.com/api/users/{user_id}", user_id, note=f"Downloading User JSON metadata")
        params = urllib.parse.urlencode(
            {"is_adult": "true" if floor == "deep" else "false"})
        entries = self._entries(
            f"https://api.v2.otobanana.com/api/users/{user_id}/casts?{params}", floor, user_id)

        return self.playlist_result(
            entries,
            playlist_id=user_id,
            thumbnails=[{"url": user_info.get("avatar_url")}],
        )
