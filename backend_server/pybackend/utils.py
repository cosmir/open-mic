import hashlib
import logging
import mimetypes
import six
import six.moves.urllib.error as urlerror
import six.moves.urllib.parse as urlparse
import six.moves.urllib.request as urlrequest
from uuid import UUID

logger = logging.getLogger()


def uuid(data):
    """Create a unique (deterministic) identifier.

    Parameters
    ----------
    data : str
        Some data to drive a deterministic hash.

    Returns
    -------
    uuid : uuid
        Generated unique identifier.
    """
    if isinstance(data, six.string_types) and six.PY2:
        data = six.BytesIO(data).buf
    elif isinstance(data, six.string_types) and six.PY3:
        data = bytearray(data, 'utf-8')

    hex_data = hashlib.md5(data).hexdigest()
    return UUID(hex=hex_data, version=4)


def check_connection(default='http://google.com', timeout=1):
    """Test the internet connection.

    Parameters
    ----------
    default : str
        URL to test; defaults to a Google IP address.

    timeout : number
        Time in seconds to wait before giving up.

    Returns
    -------
    success : bool
        True if appears to be online, else False
    """
    success = True
    try:
        surl = urlparse.quote(default, safe=':./')
        urlrequest.urlopen(surl, timeout=timeout)
    except urlerror.URLError as derp:
        success = False
        logger.debug("Network unreachable: {}".format(derp))
    return success


def mimetype_for_file(fname, strict=False):
    """Return the mimetype for a given file.

    If one is not found, defaults to 'application/octect-stream'
    """
    mtype, enc = mimetypes.guess_type(fname)
    if mtype is None and not strict:
        mtype, enc = mimetypes.guess_type('foo.bin')
    return mtype
