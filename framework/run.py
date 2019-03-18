# -*- coding: utf-8 -*-

import os
import json

import bottle
import cherrypy
from ws4py import websocket
from bottle import ServerAdapter
from bottle import static_file

from docvec import DocVec
import runtime
from mongo import MongoConnection

from tools import APP_PATH, rpc

kontext_app = bottle.Bottle()

bottle.TEMPLATE_PATH.insert(0, os.path.join(APP_PATH, 'templates', ''))
bottle.TEMPLATE_PATH.insert(1, os.path.join(APP_PATH, 'static', ''))

bottle.BaseRequest.MEMFILE_MAX = 5 * 1024 * 1024

runtime.mongo_wrapper = MongoConnection()
runtime.mongo_wrapper.set_collection('texts2')

runtime.dv = DocVec()

@kontext_app.route("/about")
def about():
    return bottle.template("about", version="0.01", user_id=0, config="yea")


@kontext_app.route("/")
def index():
    return "Go to /loader or directly /ask"


@kontext_app.route("/loader")
def index():
    return bottle.template("loader", models=runtime.dv.available_models)


@kontext_app.route("/load", method="POST")
def load_model():
    params = dict((key, bottle.request.forms.getunicode(key)) for key in bottle.request.forms)
    modelname = params['modelname']
    if modelname is None:
        print('FAILED')
        return

    print('Loading ' + modelname)
    if not modelname == runtime.modelpath:
        runtime.dv = None
        runtime.dv = DocVec()
        runtime.dv.load(modelname)

    return bottle.template("ask", list=[], original="")


@kontext_app.route("/find_mongo", method="POST")
def find_mongo():
    params = dict((key, bottle.request.forms.getunicode(key)) for key in bottle.request.forms)
    query = params['query']
    result = []
    docs = runtime.mongo_wrapper.find()
    print(docs.count())
    for doc in docs:
        filename = doc['filename']
        if query in filename:
            result.append(filename)

    return bottle.template("list", list=result)


@kontext_app.route("/ask", method="POST")
def ask():
    params = dict((key, bottle.request.forms.getunicode(key)) for key in bottle.request.forms)
    query = params['query']
    result = runtime.dv.ask(query)
    result2 = runtime.dv.ask_negative(query)

    return bottle.template("ask", list=result + result2, original=query)


@kontext_app.route('/download/<filename:path>')
def download(filename):
    print(filename)
    return static_file(filename, root='/', download=True)


class WsMessages(websocket.WebSocket):
    def received_message(self, message):
        if message:
            try:
                msg = message.data.decode(message.encoding)
                result = runtime.dv.ask(msg)
                for r in result:
                    jsonresult = json.dumps(r)
                    print(jsonresult)
                    self.send(jsonresult)
            except json.JSONDecodeError:
                self.send("This service expects valid JSON")
                return


class WsApp(object):
    @cherrypy.expose
    def ws(self):
        pass


class KontextServerAdapter(ServerAdapter):
    def __init__(self, host='127.0.0.1', port=8080, **options):
        super().__init__(host, port, **options)

    def run(self, handler):
        try:
            from cheroot.wsgi import Server as WSGIServer
        except ImportError:
            from cherrypy.wsgiserver import CherryPyWSGIServer as WSGIServer

        server = WSGIServer((self.host, self.port), handler)

        try:
            server.start()
        finally:
            server.stop()

if __name__ == "__main__":
    host = "0.0.0.0"
    port = 1337
    ws_port = port + 1

    from ws4py.server.cherrypyserver import WebSocketPlugin, WebSocketTool

    cherrypy.log.screen = False
    cherrypy.config.update({'server.socket_port': int(ws_port)})
    cherrypy.config.update({'server.socket_host': host})
    WebSocketPlugin(cherrypy.engine).subscribe()
    cherrypy.tools.websocket = WebSocketTool()

    c = {'/ws': {'tools.websocket.on': True, 'tools.websocket.handler_cls': WsMessages}}
    cherrypy.tree.mount(WsApp(), '/', config=c)
    cherrypy.engine.signals.subscribe()
    cherrypy.engine.start()

    s1 = KontextServerAdapter(host=host, port=port)

    bottle.run(kontext_app, host=host, port=port, server=s1, debug=True, quiet=False)
