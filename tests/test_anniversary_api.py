from authed_client import TestClient
from fastapi.testclient import TestClient as GuestClient

from test_chat_api import FakeLLM, make_parts
from yuwen.api import create_app


def test_anniversary_http_create_list_edit_and_disable(tmp_path):
    config, store, books = make_parts(tmp_path)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)
    with TestClient(app) as client:
        page = client.get('/anniversaries/page')
        assert page.status_code == 200
        assert '纪年' in page.text
        assert '/anniversaries/page' in client.get('/catalog').text
        created = client.post('/anniversaries', json={
            'month_day': '02-29', 'year': 2024, 'yearly': True, 'title': '闰日'
        })
        assert created.status_code == 200
        anniversary_id = created.json()['id']
        rows = client.get('/anniversaries').json()
        assert [(row['id'], row['source'], row['active']) for row in rows] == [(anniversary_id, 'manual', True)]
        updated = client.patch(f'/anniversaries/{anniversary_id}', json={'title': '第一次见面', 'active': False})
        assert updated.status_code == 200
        assert updated.json()['title'] == '第一次见面'
        assert updated.json()['active'] == 0
        assert client.get('/anniversaries').json()[0]['active'] is False
        assert client.patch(f'/anniversaries/{anniversary_id}', json={'active': True}).json()['active'] == 1
        assert client.patch('/anniversaries/999', json={'title': '不存在'}).status_code == 404
        assert store.list_anniversaries()[0]['active'] == 1


def test_anniversary_http_rejects_invalid_dates_and_requires_login(tmp_path):
    config, store, books = make_parts(tmp_path)
    app = create_app(config, store=store, llm=FakeLLM(), worldbook=books)
    with GuestClient(app) as guest:
        assert guest.get('/anniversaries').status_code == 401
        assert guest.post('/anniversaries', json={
            'month_day': '03-14', 'yearly': True, 'title': '生日'
        }).status_code == 401
        assert guest.patch('/anniversaries/1', json={'title': '改'}).status_code == 401
        assert guest.get('/anniversaries/page', follow_redirects=False).status_code == 303
    with TestClient(app) as client:
        for data in (
            {'month_day': '02-30', 'yearly': True, 'title': '坏日'},
            {'month_day': '03-14', 'yearly': False, 'title': '一次'},
            {'month_day': '03-14', 'yearly': True, 'title': ' '},
            {'month_day': '03-14', 'yearly': True, 'title': 'a' * 31},
            {'month_day': '03-14', 'yearly': 'yes', 'title': '生日'},
            {'month_day': '03-14', 'year': True, 'yearly': True, 'title': '生日'},
        ):
            assert client.post('/anniversaries', json=data).status_code == 422
        valid = client.post('/anniversaries', json={
            'month_day': '03-14', 'year': 2024, 'yearly': True, 'title': '生日'
        })
        assert valid.status_code == 200
        anniversary_id = valid.json()['id']
        assert client.patch(f'/anniversaries/{anniversary_id}', json={'yearly': False, 'year': None}).status_code == 422
        assert client.patch(f'/anniversaries/{anniversary_id}', json={'month_day': '13-01'}).status_code == 422
