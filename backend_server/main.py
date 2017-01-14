"""Flask Backend Server for managing audio content.

Please see README.md for instructions.

Starting Locally
----------------
You have two options:

  $ python main.py --port 8080 --local --debug

Or, to use GCP backend by default:

  $ dev_appserver.py .


Endpoints
---------
  - /audio : POST
  - /audio/<uri> : GET
  - /annotation/submit : POST
  - /annotation/taxonomy : GET
"""

import argparse
import datetime
from flask import Flask, request, Response, session, redirect, url_for, jsonify
from flask import send_file
from flask_cors import CORS

from functools import wraps
import io
import json
import logging
import mimetypes
import random
import requests
import os
import yaml

import pybackend.database
import pybackend.models
import pybackend.oauth
import pybackend.storage
import pybackend.urilib
import pybackend.utils

# Python 2.7 doesn't ship with `.json`?
mimetypes.add_type(mimetypes.guess_type("x.json")[0], '.json')

logging.basicConfig(level=logging.DEBUG)
app = Flask(__name__)

# Set the cloud backend
CONFIG = os.path.join(os.path.dirname(__file__), '.config.yaml')
with open(CONFIG) as fp:
    cfg = yaml.load(fp)

app.config.update(cloud=cfg['cloud'], oauth=cfg['oauth'])
app.secret_key = 'development'

SOURCE = "https://cosmir.github.io/open-mic/"
AUDIO_EXTENSIONS = set(['wav', 'ogg', 'mp3', 'au', 'aiff'])

# TODO: One of the following
#  - Whitelist localhost and `SOURCE` below.
#  - Use AppEngine for delivery of the annotator HTML?
CORS(app)
OAUTH = pybackend.oauth.OAuth(app, session)


def authenticate(f):
    """Decorate a route as requiring authentication."""
    @wraps(f)
    def decorated(*args, **kwargs):
        app.logger.info(session)
        if session.get('access_token', None) or app.config['noauth']:
            return f(*args, **kwargs)
        else:
            return redirect(url_for('login', _external=True))

    return decorated


@app.route('/login')
def login():
    """Start the OAuth login process.

    Parameters
    ----------
    complete : {yes, no}, default=yes
        Direct the OAuth login process to complete; must be 'no' in order to
        allow commandline interfaces to successfully authenticate.

    app : {google, spotify}, default=google
        Third-party application to use for OAuth handling.
    """
    callback = url_for('authorized', _external=True)
    app_name = request.args.get('app', pybackend.oauth.GOOGLE).lower()
    query = "?app={}".format(app_name)
    if request.args.get('complete', 'yes') == 'no':
        query += "&complete=no"
    return OAUTH.get(app_name).authorize(callback + query)


@app.route('/login/authorized')
def authorized():
    """Finish the OAuth login process.

    This is the callback endpoint registered with different OAuth handlers. For
    commandline interfaces, which require manual intervention, the complete=no
    parameter must be passed through the login redirect route.

    Parameters
    ----------
    complete : {yes, no}, default=yes
        Complete the OAuth login process; if 'no', returns a well-formed URL
        to be followed.
    """
    app.logger.info("{}".format(request))
    app_name = request.args.get('app')
    if request.args.get('complete', 'yes') == 'yes':
        resp = OAUTH.get(app_name).authorized_response()
        app.logger.info(resp)
        if resp is None:
            return 'Access denied: reason=%s error=%s' % (
                request.args['error_reason'],
                request.args['error_description']
            )
        session['access_token'] = (resp['access_token'], '')
        return "Successfully logged in."
    else:
        return ("To complete log-in, proceed to this URL: {}"
                .format(request.url))


@app.route('/logout')
def logout():
    """Log the user out of the current session."""
    token = session.pop('access_token', None)
    return "Success!" if token else "Not currently logged in."


@app.route("/me")
@authenticate
def me():
    """Demonstrate that the user has been successfully logged in."""
    import ipdb;ipdb.set_trace()
    return jsonify({"data": 'todo'})


@app.route('/api/v0.1/audio', methods=['POST'])
@authenticate
def audio_upload():
    """
    To POST files to this endpoint:

    $ curl -F "audio=@some_file.mp3" localhost:8080/api/v0.1/audio

    TODOs:
      - Store user data (who uploaded this? IP address?)
      - File metadata
    """
    audio_data = request.files['audio']
    file_ext = os.path.splitext(audio_data.filename)[-1][1:]
    if file_ext not in AUDIO_EXTENSIONS:
        app.logger.exception('Attempted upload of unsupported filetype.')
        return 'Filetype not supported.', 400

    bytestring = audio_data.stream.read()
    app.logger.info("Uploaded data: type={}, len={}"
                    .format(type(bytestring), len(bytestring)))

    # Copy to cloud storage
    store = pybackend.storage.Storage(
        project=app.config['cloud']['project'],
        **app.config['cloud']['storage'])

    gid = str(pybackend.utils.uuid(bytestring))
    store.put(gid, bytestring)

    # Index in datastore
    # Keep things like extension, storage platform, mimetype, etc.
    dbase = pybackend.database.Database(
        project=app.config['cloud']['project'],
        **app.config['cloud']['database'])

    uri = pybackend.urilib.join('audio', gid)
    record = dict(gid=gid, file_ext=file_ext,
                  created=str(datetime.datetime.now()))

    dbase.put(uri, record)
    record.update(
        uri=uri, message="Received {} bytes of data.".format(len(bytestring)))

    resp = Response(json.dumps(record), status=200,
                    mimetype=mimetypes.types_map[".json"])
    resp.headers['Link'] = SOURCE
    return resp


@app.route('/api/v0.1/audio/<gid>', methods=['GET'])
@authenticate
def audio_download(gid):
    """
    To GET responses from this endpoint:

    $ curl -XGET localhost:8080/audio/bbdde322-c604-4753-b828-9fe8addf17b9
    """
    dbase = pybackend.database.Database(
        project=app.config['cloud']['project'],
        **app.config['cloud']['database'])

    uri = pybackend.urilib.join('audio', gid)

    entity = dbase.get(uri)
    if entity is None:
        msg = "Resource not found: {}".format(uri)
        app.logger.info(msg)
        resp = Response(
            json.dumps(dict(message=msg)),
            status=404)

    else:
        store = pybackend.storage.Storage(
            project=app.config['cloud']['project'],
            **app.config['cloud']['storage'])

        data = store.get(entity['gid'])
        app.logger.debug("Returning {} bytes".format(len(data)))

        filename = os.path.extsep.join([entity['gid'], entity['file_ext']])
        resp = send_file(
            io.BytesIO(data),
            attachment_filename=filename,
            mimetype=pybackend.utils.mimetype_for_file(filename))

    resp.headers['Link'] = SOURCE
    return resp


@app.route('/api/v0.1/annotation/submit', methods=['POST'])
@authenticate
def annotation_submit():
    """
    To POST data to this endpoint:

    $ curl -H "Content-type: application/json" \
        -X POST localhost:8080/annotation/submit \
        -d '{"message":"Hello Data"}'
    """
    if request.headers['Content-Type'] == 'application/json':
        app.logger.info("Received Annotation:\n{}"
                        .format(json.dumps(request.json, indent=2)))
        # Do a thing with the annotation
        # Return some progress stats?
        data = json.dumps(dict(message='Success!'))
        status = 200

        db = pybackend.database.Database(
            project=app.config['cloud']['project'],
            **app.config['cloud']['database'])
        gid = str(pybackend.utils.uuid(json.dumps(request.json)))
        uri = pybackend.urilib.join('annotation', gid)
        record = pybackend.models.AnnotationResponse(
            created=str(datetime.datetime.now()),
            response=request.json,
            user_id='anonymous')
        db.put(uri, record.flatten())
    else:
        status = 400
        data = json.dumps(dict(message='Invalid Content-Type; '
                                       'only accepts application/json'))

    resp = Response(
        data, status=status, mimetype=mimetypes.types_map[".json"])
    resp.headers['Link'] = SOURCE
    return resp


def get_taxonomy():
    tax_url = ("https://raw.githubusercontent.com/cosmir/open-mic/"
               "master/data/instrument_taxonomy_v0.json")
    res = requests.get(tax_url)
    values = []
    try:
        schema = res.json()
        values = schema['tag_open_mic_instruments']['value']['enum']
    except BaseException as derp:
        app.logger.error("Failed loading taxonomy: {}".format(derp))

    return values


@app.route('/api/v0.1/annotation/taxonomy', methods=['GET'])
def annotation_taxonomy():
    """
    To fetch data at this endpoint:

    $ curl -X GET localhost:8080/annotation/taxonomy
    """
    instruments = get_taxonomy()
    status = 200 if instruments else 400

    resp = Response(json.dumps(instruments), status=status)
    resp.headers['Link'] = SOURCE
    return resp


@app.route('/api/v0.1/task', methods=['GET'])
@authenticate
def next_task():
    """
    To fetch data at this endpoint:

    $ curl -X GET localhost:8080/task
    """
    db = pybackend.database.Database(
        project=app.config['cloud']['project'],
        **app.config['cloud']['database'])

    random_uri = random.choice(list(db.uris(kind='audio')))
    audio_url = "{scheme}://{netloc}/api/v0.1/audio/{gid}".format(
        gid=pybackend.urilib.split(random_uri)[1],
        **app.config['cloud']['annotator'])

    task = dict(feedback="none",
                visualization=random.choice(['waveform', 'spectrogram']),
                proximityTag=[],
                annotationTag=get_taxonomy(),
                url=audio_url,
                numRecordings='?',
                recordingIndex=random_uri,
                tutorialVideoURL="https://www.youtube.com/embed/Bg8-83heFRM",
                alwaysShowTags=True)
    data = json.dumps(dict(task=task))
    app.logger.debug("Returning:\n{}".format(data))
    resp = Response(data)
    resp.headers['Link'] = SOURCE
    return resp


@app.errorhandler(500)
def server_error(e):
    logging.exception('An error occurred during a request.')
    return 'An internal error occurred.', 500


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--port", type=int, default=8080,
        help="Port on which to serve.")
    parser.add_argument(
        "--config", type=str,
        help="Specific config file to use.")
    parser.add_argument(
        "--noauth",
        action='store_true', help="Disable authentication, for testing.")
    parser.add_argument(
        "--debug",
        action='store_true',
        help="Run the Flask application in debug mode.")

    args = parser.parse_args()
    app.config['noauth'] = args.noauth
    if args.config:
        cfg_file = os.path.join(os.path.dirname(__file__), args.config)
        with open(cfg_file) as fp:
            cfg = yaml.load(fp)

        app.config.update(cloud=cfg['cloud'], oauth=cfg['oauth'])

    app.run(debug=args.debug, port=args.port)
