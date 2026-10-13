"""已登录的测试客户端；登录行为本身由独立测试覆盖。"""

from fastapi.testclient import TestClient as _TestClient

from yuwen.auth import COOKIE_NAME


class TestClient(_TestClient):
    def __enter__(self):
        client = super().__enter__()
        token = self.app.state.auth_service.create_session()
        client.cookies.set(COOKIE_NAME, token)
        return client
