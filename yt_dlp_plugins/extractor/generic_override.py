import re
import xml.etree.ElementTree
import urllib.parse

from yt_dlp.extractor.generic import GenericIE as _OriginalGenericIE
from yt_dlp.compat import compat_etree_fromstring
from yt_dlp.networking.exceptions import HTTPError
from yt_dlp.networking.impersonate import ImpersonateTarget
from yt_dlp.cookies import LenientSimpleCookie
from yt_dlp.utils import (
    ExtractorError,
    UnsupportedError,
    determine_ext,
    extract_basic_auth,
    filter_dict,
    is_html,
    merge_dicts,
    smuggle_url,
    traverse_obj,
    unified_timestamp,
    unsmuggle_url,
    update_url,
    urlhandle_detect_ext,
)
from yt_dlp.utils._utils import _UnsafeExtensionError


class _GenericIE(_OriginalGenericIE, plugin_name='expected404'):
    def _real_extract(self, url):
        if url.startswith('//'):
            return self.url_result(self.http_scheme() + url)

        parsed_url = urllib.parse.urlparse(url)
        if not parsed_url.scheme:
            default_search = self.get_param('default_search')
            if default_search is None:
                default_search = 'fixup_error'

            if default_search in ('auto', 'auto_warning', 'fixup_error'):
                if re.match(r'[^\s/]+\.[^\s/]+/', url):
                    self.report_warning('The url doesn\'t specify the protocol, trying with https')
                    return self.url_result('https://' + url)
                elif default_search != 'fixup_error':
                    if default_search == 'auto_warning':
                        if re.match(r'^(?:url|URL)$', url):
                            raise ExtractorError(
                                f'Invalid URL:  {url!r} . Call yt-dlp like this:  yt-dlp -v "https://www.youtube.com/watch?v=YE7VzlLtp-4"  ',
                                expected=True)
                        else:
                            self.report_warning(
                                f'Falling back to youtube search for  {url} . Set --default-search "auto" to suppress this warning.')
                    return self.url_result('ytsearch:' + url)

            if default_search in ('error', 'fixup_error'):
                raise ExtractorError(f'{url!r} is not a valid URL', expected=True)
            else:
                if ':' not in default_search:
                    default_search += ':'
                return self.url_result(default_search + url)

        original_url = url
        url, smuggled_data = unsmuggle_url(url, {})
        force_videoid = None
        is_intentional = smuggled_data.get('to_generic')
        if 'force_videoid' in smuggled_data:
            force_videoid = smuggled_data['force_videoid']
            video_id = force_videoid
        else:
            video_id = self._generic_id(url)

        impersonate = self._configuration_arg('impersonate', ['false'])
        if 'false' in impersonate:
            impersonate = None

        try:
            full_response = self._request_webpage(url, video_id, headers=filter_dict({
                'Accept-Encoding': 'identity',
                'Referer': smuggled_data.get('referer'),
            }), impersonate=impersonate, expected_status=404)
        except ExtractorError as e:
            if not isinstance(e.cause, HTTPError) or e.cause.status != 403:
                raise
            res = e.cause.response
            already_impersonating = res.extensions.get('impersonate') is not None
            if already_impersonating or (
                res.get_header('cf-mitigated') != 'challenge'
                and b'<title>Attention Required! | Cloudflare</title>' not in res.read()
            ):
                raise
            cf_cookie_domain = traverse_obj(
                LenientSimpleCookie(res.get_header('set-cookie')), ('__cf_bm', 'domain'))
            if cf_cookie_domain:
                self.write_debug(f'Clearing __cf_bm cookie for {cf_cookie_domain}')
                self.cookiejar.clear(domain=cf_cookie_domain, path='/', name='__cf_bm')
            msg = 'Got HTTP Error 403 caused by Cloudflare anti-bot challenge; '
            if not self._downloader._impersonate_target_available(ImpersonateTarget()):
                msg += ('see  https://github.com/yt-dlp/yt-dlp#impersonation  for '
                        'how to install the required impersonation dependency, and ')
            raise ExtractorError(
                f'{msg}try again with  --extractor-args "generic:impersonate"', expected=True)

        new_url = full_response.url
        if new_url != extract_basic_auth(url)[0]:
            self.report_following_redirect(new_url)
            if force_videoid:
                new_url = smuggle_url(new_url, {'force_videoid': force_videoid})
            return self.url_result(new_url)

        info_dict = {
            'id': video_id,
            'title': self._generic_title(url),
            'timestamp': unified_timestamp(full_response.headers.get('Last-Modified')),
        }

        content_type = full_response.headers.get('Content-Type', '').lower()
        m = re.match(r'(?P<type>audio|video|application(?=/(?:ogg$|(?:vnd\.apple\.|x-)?mpegurl)))/(?P<format_id>[^;\s]+)', content_type)
        if m:
            self.report_detected('direct video link')
            headers = filter_dict({'Referer': smuggled_data.get('referer')})
            format_id = str(m.group('format_id'))
            ext = determine_ext(url, default_ext=None) or urlhandle_detect_ext(full_response)
            subtitles = {}
            if format_id.endswith('mpegurl') or ext == 'm3u8':
                formats, subtitles = self._extract_m3u8_formats_and_subtitles(url, video_id, 'mp4', headers=headers)
            elif format_id == 'f4m' or ext == 'f4m':
                formats = self._extract_f4m_formats(url, video_id, headers=headers)
            else:
                formats = [{
                    'format_id': format_id,
                    'url': url,
                    'ext': ext,
                    'vcodec': 'none' if m.group('type') == 'audio' else None,
                }]
                info_dict['direct'] = True
            info_dict.update({
                'formats': formats,
                'subtitles': subtitles,
                'http_headers': headers or None,
            })
            self._extra_manifest_info(info_dict, url)
            return info_dict

        if not self.get_param('test', False) and not is_intentional:
            force = self.get_param('force_generic_extractor', False)
            self.report_warning('%s generic information extractor' % ('Forcing' if force else 'Falling back on'))

        first_bytes = full_response.read(512)

        if first_bytes.startswith(b'#EXTM3U'):
            self.report_detected('M3U playlist')
            info_dict['formats'], info_dict['subtitles'] = self._extract_m3u8_formats_and_subtitles(url, video_id, 'mp4')
            self._extra_manifest_info(info_dict, url)
            return info_dict

        if not is_html(first_bytes):
            self.report_warning(
                'URL could be a direct video link, returning it as such.')
            ext = determine_ext(url)
            if ext not in _UnsafeExtensionError.ALLOWED_EXTENSIONS:
                ext = 'unknown_video'
            info_dict.update({
                'direct': True,
                'url': url,
                'ext': ext,
            })
            return info_dict

        webpage = self._webpage_read_content(
            full_response, url, video_id, prefix=first_bytes)

        if '<title>DPG Media Privacy Gate</title>' in webpage:
            webpage = self._download_webpage(url, video_id)

        self.report_extraction(video_id)

        try:
            try:
                doc = compat_etree_fromstring(webpage)
            except xml.etree.ElementTree.ParseError:
                doc = compat_etree_fromstring(webpage.encode())
            if doc.tag == 'rss':
                self.report_detected('RSS feed')
                return self._extract_rss(url, video_id, doc)
            elif doc.tag == 'SmoothStreamingMedia':
                info_dict['formats'], info_dict['subtitles'] = self._parse_ism_formats_and_subtitles(doc, url)
                self.report_detected('ISM manifest')
                return info_dict
            elif re.match(r'^(?:{[^}]+})?smil$', doc.tag):
                smil = self._parse_smil(doc, url, video_id)
                self.report_detected('SMIL file')
                return smil
            elif doc.tag == '{http://xspf.org/ns/0/}playlist':
                self.report_detected('XSPF playlist')
                return self.playlist_result(
                    self._parse_xspf(
                        doc, video_id, xspf_url=url,
                        xspf_base_url=new_url),
                    video_id)
            elif re.match(r'(?i)^(?:{[^}]+})?MPD$', doc.tag):
                info_dict['formats'], info_dict['subtitles'] = self._parse_mpd_formats_and_subtitles(
                    doc,
                    mpd_base_url=update_url(new_url, query=None, fragment=None).rpartition('/')[0],
                    mpd_url=url)
                info_dict['live_status'] = 'is_live' if doc.get('type') == 'dynamic' else None
                self._extra_manifest_info(info_dict, url)
                self.report_detected('DASH manifest')
                return info_dict
            elif re.match(r'^{http://ns\.adobe\.com/f4m/[12]\.0}manifest$', doc.tag):
                info_dict['formats'] = self._parse_f4m_formats(doc, url, video_id)
                self.report_detected('F4M manifest')
                return info_dict
        except xml.etree.ElementTree.ParseError:
            pass

        info_dict.update({
            'title': self._generic_title('', webpage, default='video'),
            'description': self._og_search_description(webpage, default=None),
            'thumbnail': self._og_search_thumbnail(webpage, default=None),
            'age_limit': self._rta_search(webpage),
        })

        self._downloader.write_debug('Looking for embeds')
        embeds = list(self._extract_embeds(original_url, webpage, urlh=full_response, info_dict=info_dict))
        if len(embeds) == 1:
            return merge_dicts(embeds[0], info_dict)
        elif embeds:
            return self.playlist_result(embeds, **info_dict)
        raise UnsupportedError(url)