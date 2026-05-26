from elasticsearch import Elasticsearch

from .config import ES_CA_CERTS, ES_PASSWD, ES_URL, ES_USER, ES_VERIFY_CERTS


def get_es() -> Elasticsearch:
    kwargs: dict = {
        "verify_certs": ES_VERIFY_CERTS,
    }
    if ES_USER or ES_PASSWD:
        kwargs["basic_auth"] = (ES_USER, ES_PASSWD)
    if ES_CA_CERTS:
        kwargs["ca_certs"] = ES_CA_CERTS
    return Elasticsearch(ES_URL, **kwargs)
