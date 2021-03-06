# coding=utf-8
"""Tests for Pulp's `content applicability`_ feature.

.. _content applicability:
    http://docs.pulpproject.org/dev-guide/integration/rest-api/consumer/applicability.html
"""
import unittest
from types import MappingProxyType
from urllib.parse import urljoin

from jsonschema import validate
from pulp_smash import api, config
from pulp_smash.pulp2.constants import (
    CONSUMERS_ACTIONS_CONTENT_REGENERATE_APPLICABILITY_PATH,
    CONSUMERS_CONTENT_APPLICABILITY_PATH,
    CONSUMERS_PATH,
    REPOSITORY_PATH,
)
from pulp_smash.pulp2.utils import (
    publish_repo,
    sync_repo,
)

from pulp_2_tests.constants import (
    RPM2_DATA,
    RPM_DATA,
    RPM_UNSIGNED_FEED_URL,
)
from pulp_2_tests.tests.rpm.api_v2.utils import (
    gen_consumer,
    gen_distributor,
    gen_repo,
)
from pulp_2_tests.tests.rpm.utils import set_up_module as setUpModule  # pylint:disable=unused-import

# MappingProxyType is used to make an immutable dict.
RPM_WITH_ERRATUM_METADATA = MappingProxyType({
    'name': RPM_DATA['name'],
    'epoch': RPM_DATA['epoch'],
    'version': RPM_DATA['version'],
    'release': int(RPM_DATA['release']),
    'arch': RPM_DATA['arch'],
    'vendor': RPM_DATA['metadata']['vendor'],
})
"""Metadata for an RPM with an associated erratum."""

RPM_WITHOUT_ERRATUM_METADATA = MappingProxyType({
    'name': RPM2_DATA['name'],
    'epoch': RPM2_DATA['epoch'],
    'version': RPM2_DATA['version'],
    'release': int(RPM2_DATA['release']),
    'arch': RPM2_DATA['arch'],
    'vendor': RPM2_DATA['metadata']['vendor'],
})
"""Metadata for an RPM without an associated erratum."""

CONTENT_APPLICABILITY_REPORT_SCHEMA = {
    '$schema': 'http://json-schema.org/schema#',
    'title': 'Content Applicability Report',
    'description': (
        'Derived from: http://docs.pulpproject.org/'
        'dev-guide/integration/rest-api/consumer/applicability.html'
        '#query-content-applicability'
    ),
    'type': 'array',
    'items': {
        'type': 'object',
        'properties': {
            'applicability': {
                'type': 'object',
                'properties': {
                    'erratum': {
                        'type': 'array',
                        'items': {'type': 'string'}
                    },
                    'modulemd': {
                        'type': 'array',
                        'items': {'type': 'string'}
                    },
                    'rpm': {
                        'type': 'array',
                        'items': {'type': 'string'}
                    }
                }
            },
            'consumers': {
                'type': 'array',
                'items': {'type': 'string'}
            }
        }
    }
}
"""A schema for a content applicability report for a consumer.

Schema now includes modulemd profiles:

* `Pulp #3925 <https://pulp.plan.io/issues/3925>`_
"""


class BasicTestCase(unittest.TestCase):
    """Perform simple applicability generation tasks."""

    @classmethod
    def setUpClass(cls):
        """Create and sync a repository.

        The regular test methods that run after this can create consumers that
        bind to this repository.
        """
        cls.cfg = config.get_config()
        client = api.Client(cls.cfg, api.json_handler)
        body = gen_repo()
        body['importer_config']['feed'] = RPM_UNSIGNED_FEED_URL
        body['distributors'] = [gen_distributor()]
        cls.repo = client.post(REPOSITORY_PATH, body)
        try:
            cls.repo = client.get(cls.repo['_href'], params={'details': True})
            sync_repo(cls.cfg, cls.repo)
            publish_repo(cls.cfg, cls.repo)
            cls.repo = client.get(cls.repo['_href'], params={'details': True})
        except:  # noqa:E722
            cls.tearDownClass()
            raise

    @classmethod
    def tearDownClass(cls):
        """Delete the repository created by :meth:`setUpClass`."""
        api.Client(cls.cfg).delete(cls.repo['_href'])

    def test_positive(self):
        """Verify content is made available when appropriate.

        Specifically, do the following:

        1. Create a consumer.
        2. Bind the consumer to the repository created in :meth:`setUpClass`.
        3. Create a consumer profile where:

           * two packages are installed,
           * both packages' versions are lower than what's offered by the
             repository,
           * one of the corresponding packages in the repository has an
             applicable erratum, and
           * the other corresponding package in the repository doesn't have an
             applicable erratum.

        4. Regenerate applicability for the consumer.
        5. Fetch applicability for the consumer. Verify that both packages are
           listed as eligible for an upgrade.
        """
        # Create a consumer.
        client = api.Client(self.cfg, api.json_handler)
        consumer = client.post(CONSUMERS_PATH, gen_consumer())
        self.addCleanup(client.delete, consumer['consumer']['_href'])

        # Bind the consumer.
        client.post(urljoin(consumer['consumer']['_href'], 'bindings/'), {
            'distributor_id': self.repo['distributors'][0]['id'],
            'notify_agent': False,
            'repo_id': self.repo['id'],
        })

        # Create a consumer profile.
        rpm_with_erratum_metadata = RPM_WITH_ERRATUM_METADATA.copy()
        rpm_with_erratum_metadata['version'] = '4.0'
        rpm_without_erratum_metadata = RPM_WITHOUT_ERRATUM_METADATA.copy()
        rpm_without_erratum_metadata['version'] = '0.0.1'
        client.post(urljoin(consumer['consumer']['_href'], 'profiles/'), {
            'content_type': 'rpm',
            'profile': [
                rpm_with_erratum_metadata,
                rpm_without_erratum_metadata,
            ]
        })

        # Regenerate applicability.
        client.post(CONSUMERS_ACTIONS_CONTENT_REGENERATE_APPLICABILITY_PATH, {
            'consumer_criteria': {
                'filters': {'id': {'$in': [consumer['consumer']['id']]}}
            }
        })

        # Fetch applicability.
        applicability = client.post(CONSUMERS_CONTENT_APPLICABILITY_PATH, {
            'criteria': {
                'filters': {'id': {'$in': [consumer['consumer']['id']]}}
            },
        })
        validate(applicability, CONTENT_APPLICABILITY_REPORT_SCHEMA)
        with self.subTest(comment='verify erratum listed in report'):
            self.assertEqual(
                len(applicability[0]['applicability']['erratum']),
                1,
                applicability[0]['applicability']['erratum'],
            )
        with self.subTest(comment='verify modulemd listed in report'):
            self.assertEqual(
                len(applicability[0]['applicability']['modulemd']),
                0,
                applicability[0]['applicability']['modulemd'],
            )
        with self.subTest(comment='verify RPMs listed in report'):
            self.assertEqual(
                len(applicability[0]['applicability']['rpm']),
                2,
                applicability[0]['applicability']['rpm'],
            )
        with self.subTest(comment='verify consumers listed in report'):
            self.assertEqual(
                applicability[0]['consumers'],
                [consumer['consumer']['id']],
            )

    def test_negative(self):
        """Verify content isn't made available when appropriate.

        Do the same as :meth:`test_positive`, except that both packages'
        versions are equal to what's offered by the repository.
        """
        # Create a consumer.
        client = api.Client(self.cfg, api.json_handler)
        consumer = client.post(CONSUMERS_PATH, gen_consumer())
        self.addCleanup(client.delete, consumer['consumer']['_href'])

        # Bind the consumer.
        client.post(urljoin(consumer['consumer']['_href'], 'bindings/'), {
            'distributor_id': self.repo['distributors'][0]['id'],
            'notify_agent': False,
            'repo_id': self.repo['id'],
        })

        # Create a consumer profile.
        client.post(urljoin(consumer['consumer']['_href'], 'profiles/'), {
            'content_type': 'rpm',
            'profile': [
                # The JSON serializer can't handle MappingProxyType objects.
                dict(RPM_WITH_ERRATUM_METADATA),
                dict(RPM_WITHOUT_ERRATUM_METADATA),
            ]
        })

        # Regenerate applicability.
        client.post(CONSUMERS_ACTIONS_CONTENT_REGENERATE_APPLICABILITY_PATH, {
            'consumer_criteria': {
                'filters': {'id': {'$in': [consumer['consumer']['id']]}}
            }
        })

        # Fetch applicability.
        applicability = client.post(CONSUMERS_CONTENT_APPLICABILITY_PATH, {
            'content_types': ['rpm'],
            'criteria': {
                'filters': {'id': {'$in': [consumer['consumer']['id']]}}
            },
        })
        validate(applicability, CONTENT_APPLICABILITY_REPORT_SCHEMA)
        with self.subTest(comment='verify RPMs listed in report'):
            self.assertEqual(len(applicability[0]['applicability']['rpm']), 0)
        with self.subTest(comment='verify consumers listed in report'):
            self.assertEqual(
                applicability[0]['consumers'],
                [consumer['consumer']['id']],
            )
