from flask import Flask
import requests
import os
import re
import json
import urllib

app = Flask('hurtle-cc-api')

uri = os.environ.get('URI', False)
namespace = os.environ.get('NAMESPACE', False)
if not uri:
    raise AttributeError('CC not configured properly! no URI found!')
if not namespace:
    raise AttributeError('CC not configured properly! no NAMESPACE found!')


def print_response(response):
    if response.content:
        print '> %i: %s' % (response.status_code, response.content)
    else:
        print '> %i' % response.status_code


def get_auth_heads():
    uri = os.environ.get('URI', False)
    if not uri:
        raise AttributeError('No URI defined!')

    url = uri + '/oauth/authorize?response_type=token&client_id=openshift-challenging-client'
    print 'curl -v -X GET %s' % url
    response = requests.get(url,
                            auth=(os.environ.get('USERNAME'), os.environ.get('PASSWORD')), verify=False,
                            allow_redirects=False)

    if not response.status_code == 302:
        raise AttributeError('Login Error')

    location = response.headers['Location']
    ops_token = re.search('access_token=([^&]*)', location).group(1)
    auth_heads = dict()
    auth_heads['Authorization'] = 'Bearer ' + ops_token

    return auth_heads


@app.route('/')
def home():
    return '', 200


# curl -X GET $URL/build/$NAME/ -> gets a list of all builds
@app.route('/build/<name>', methods=['GET'])
def list_builds(name):
    auth_heads = get_auth_heads()

    url = uri + '/oapi/v1/namespaces/%s/builds?labelSelector=%s' % (namespace, urllib.quote('app=%s' % name))
    print 'curl -v -X GET %s' % url
    response = requests.get(url, headers=auth_heads, verify=False)
    print_response(response)

    data = json.loads(response.content)
    builds = []
    if isinstance(data['items'], list):
        for build in data['items']:
            try:
                builds.append(build['metadata']['name'])
            except KeyError:
                pass

    return json.dumps(builds), response.status_code


# curl -X GET $URL/build/$NAME/$BUILD_ID/ -> gets the status of the build
@app.route('/build/<name>/<build>', methods=['GET'])
def get_build(name, build):
    auth_heads = get_auth_heads()

    url = uri + '/oapi/v1/namespaces/%s/builds/%s' % (namespace, build)
    print 'curl -v -X GET %s' % url
    response = requests.get(url, headers=auth_heads,
                            verify=False)
    print_response(response)

    data = json.loads(response.content)

    return json.dumps(data['status']), response.status_code


# curl -X POST $URL/build/self -> re-builds CC (also re-provisions)
# curl -X POST $URL/build/$NAME -> re-builds the buildConfig for $NAME, does not trigger redeployment!
@app.route('/build/<name>', methods=['POST'])
def build(name):
    if name == 'self':
        name = 'hurtle-cc-api'

    if name == '':
        return 'You must provide a valid name!', 404

    auth_heads = get_auth_heads()

    url = uri + '/oapi/v1/namespaces/%s/builds' % namespace
    print 'curl -v -X GET %s?labelSelector=app=build' % url
    response = requests.get(
            url,
            headers=auth_heads, verify=False, params={'labelSelector': 'app=%s' % name})
    print_response(response)

    data = json.loads(response.content)

    if len(data['items']) == 0:
        return 'BuildConfig with name %s not found!' % name, 404

    latest_build_number = 0
    latest_build = None
    for current_build in data['items']:

        try:
            if int(current_build['metadata']['annotations']['openshift.io/build.number']) > latest_build_number:
                latest_build_number = int(current_build['metadata']['annotations']['openshift.io/build.number'])
                latest_build = current_build
        except KeyError:
            pass
    new_build_number = latest_build_number + 1
    latest_build['metadata']['annotations']['openshift.io/build.number'] = str(new_build_number)
    latest_build['metadata']['name'] = latest_build['metadata']['name'].replace(str(latest_build_number),
                                                                                str(new_build_number))
    del latest_build['metadata']['selfLink']
    del latest_build['metadata']['uid']
    del latest_build['metadata']['resourceVersion']
    del latest_build['status']
    latest_build['apiVersion'] = 'v1'
    latest_build['kind'] = 'Build'

    json_payload = json.dumps(latest_build)
    url = uri + '/oapi/v1/namespaces/test/builds'
    print 'curl -v -X POST %s -d \'%s\'' % (url, json_payload)
    response = requests.post(url, headers=auth_heads, verify=False,
                             data=json_payload)
    print_response(response)

    return json.dumps({'build_name': latest_build['metadata']['name']}), response.status_code


# curl -X POST $URL/update/self -> re-provisions CC (no effect)
# curl -X POST $URL/update/$NAME -> re-provisions the deploymentConfig $NAME, do this after a /build/$NAME was triggered
@app.route('/update/<name>', methods=['POST'])
def update(name):
    if name == 'self':
        return 'CC does not support update on itself, use build instead!', 500

    auth_heads = get_auth_heads()

    url = '%s/oapi/v1/namespaces/%s/deploymentconfigs/%s' % (uri, namespace, name)
    print 'curl -v -X GET %s' % url
    r = requests.get(url,
                     headers=auth_heads, verify=False)
    print_response(r)

    if r.status_code != 200:
        return 'DeploymentConfig %s not found!' % name, 404
    deployment_config = json.loads(r.content)

    deployment_config['status']['latestVersion'] += 1
    new_env = []
    found = False
    for env in deployment_config['spec']['template']['spec']['containers'][0]['env']:
        if env['name'] == 'VERSION':
            env['value'] = deployment_config['status']['latestVersion']
            found = True
        new_env.append(env)
    if not found:
        new_env.append({'name': 'VERSION', 'value': deployment_config['status']['latestVersion']})
    deployment_config['spec']['template']['spec']['containers'][0]['env'] = new_env

    deployment_config_json = json.dumps(deployment_config)

    url = '%s/oapi/v1/namespaces/%s/deploymentconfigs/%s' % (uri, namespace, name)
    print 'curl -v -X PUT %s -d \'%s\'' % (url, deployment_config_json)
    r = requests.put(url, headers=auth_heads,
                     verify=False, data=deployment_config_json)
    print_response(r)

    if r.status_code == 200:
        return json.dumps(deployment_config), 200
    else:
        return 'Something went wrong!', r.status_code


def server(host, port):
    app.run(host=host, port=port, debug=False)
