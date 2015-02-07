from cattle import from_env
import pytest
import random
import requests
import os
import time
import logging

log = logging.getLogger()

TEST_IMAGE_UUID = os.environ.get('CATTLE_TEST_AGENT_IMAGE',
                                 'docker:cattle/test-agent:v7')

DEFAULT_TIMEOUT = 45


@pytest.fixture(scope='session')
def cattle_url():
    default_url = 'http://localhost:8080/v1/schemas'
    return os.environ.get('CATTLE_TEST_URL', default_url)


@pytest.fixture(autouse=True, scope='session')
def cleanup(client):
    to_delete = []
    for i in client.list_instance(state='running'):
        try:
            if i.name.startswith('test-'):
                to_delete.append(i)
        except AttributeError:
            pass

    delete_all(client, to_delete)


@pytest.fixture(scope='session')
def client(cattle_url):
    client = from_env(url=cattle_url)
    assert client.valid()
    return client


@pytest.fixture(scope='session')
def admin_client(cattle_url):
    admin_client = from_env(url=cattle_url)
    assert admin_client.valid()
    return admin_client


@pytest.fixture
def test_name():
    return random_str()


@pytest.fixture
def random_str():
    return 'test-{0}'.format(random_num())


@pytest.fixture
def random_num():
    return random.randint(0, 1000000)


def wait_all_success(client, items, timeout=DEFAULT_TIMEOUT):
    result = []
    for item in items:
        item = client.wait_success(item, timeout=timeout)
        result.append(item)

    return result


@pytest.fixture
def managed_network(client):
    networks = client.list_network(uuid='managed-docker0')
    assert len(networks) == 1

    return networks[0]


@pytest.fixture
def one_per_host(client, test_name, managed_network):
    instances = []
    hosts = client.list_host(kind='docker', removed_null=True)
    assert len(hosts) > 2

    for host in hosts:
        c = client.create_container(name=test_name,
                                    ports=['3000:3000'],
                                    networkIds=managed_network.id,
                                    imageUuid=TEST_IMAGE_UUID,
                                    requestedHostId=host.id)
        instances.append(c)

    instances = wait_all_success(client, instances, timeout=120)

    for i in instances:
        ports = i.ports()
        assert len(ports) == 1
        port = ports[0]

        assert port.privatePort == 3000
        assert port.publicPort == 3000

        ping_port(port)

    return instances


def delete_all(client, items):
    wait_for = []
    for i in items:
        client.delete(i)
        wait_for.append(client.reload(i))

    wait_all_success(client, items)


def get_port_content(port, path, params={}):
    assert port.publicPort is not None
    assert port.publicIpAddressId is not None

    url = 'http://{}:{}/{}'.format(port.publicIpAddress().address,
                                   port.publicPort,
                                   path)

    e = None
    for i in range(60):
        try:
            return requests.get(url, params=params, timeout=5).text
        except Exception as e1:
            e = e1
            log.exception('Failed to call %s', url)
            time.sleep(1)
            pass

    if e is not None:
        raise e

    raise Exception('failed to call url {0} for port'.format(url))


def ping_port(port):
    pong = get_port_content(port, 'ping')
    assert pong == 'pong'


def ping_link(src, link_name, var=None, value=None):
    src_port = src.ports()[0]
    links = src.instanceLinks()

    assert len(links) == 1
    assert len(links[0].ports) == 1
    assert links[0].linkName == link_name

    for i in range(3):
        from_link = get_port_content(src_port, 'get', params={
            'link': link_name,
            'path': 'env?var=' + var,
            'port': links[0].ports[0].privatePort
        })

        if from_link == value:
            continue
        else:
            time.sleep(1)

    assert from_link == value
