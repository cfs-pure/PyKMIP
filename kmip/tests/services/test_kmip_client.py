# Copyright (c) 2014 The Johns Hopkins University/Applied Physics Laboratory
# All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from testtools import TestCase

from subprocess import Popen

import os
import sys
import time

from kmip.core.enums import AttributeType
from kmip.core.enums import CredentialType
from kmip.core.enums import CryptographicAlgorithm
from kmip.core.enums import CryptographicUsageMask
from kmip.core.enums import ObjectType
from kmip.core.enums import Operation as OperationEnum
from kmip.core.enums import KeyFormatType
from kmip.core.enums import ResultStatus
from kmip.core.enums import ResultReason

from kmip.core.errors import KMIPServerSuicideError
from kmip.core.errors import KMIPServerZombieError

from kmip.core.factories.attributes import AttributeFactory
from kmip.core.factories.credentials import CredentialFactory
from kmip.core.factories.secrets import SecretFactory

from kmip.core.messages.messages import RequestBatchItem
from kmip.core.messages.messages import ResponseBatchItem
from kmip.core.messages.messages import ResponseMessage
from kmip.core.messages.contents import Operation
from kmip.core.messages.payloads.create_key_pair import \
    CreateKeyPairRequestPayload, CreateKeyPairResponsePayload

from kmip.core.objects import Attribute
from kmip.core.objects import CommonTemplateAttribute
from kmip.core.objects import PrivateKeyTemplateAttribute
from kmip.core.objects import PublicKeyTemplateAttribute
from kmip.core.objects import TemplateAttribute

from kmip.core.secrets import SymmetricKey

from kmip.services.kmip_client import KMIPProxy
from kmip.services.results import CreateKeyPairResult

import kmip.core.utils as utils


class TestKMIPClientIntegration(TestCase):
    STARTUP_TIME = 1.0
    SHUTDOWN_TIME = 0.1
    KMIP_PORT = 9090
    CA_CERTS_PATH = os.path.normpath(os.path.join(os.path.dirname(
        os.path.abspath(__file__)), '../../demos/certs/server.crt'))

    def setUp(self):
        super(TestKMIPClientIntegration, self).setUp()

        self.attr_factory = AttributeFactory()
        self.cred_factory = CredentialFactory()
        self.secret_factory = SecretFactory()

        # Set up the KMIP server process
        path = os.path.join(os.path.dirname(__file__), os.path.pardir,
                            'utils', 'server.py')
        self.server = Popen(['python', '{0}'.format(path), '-p',
                             '{0}'.format(self.KMIP_PORT)], stderr=sys.stdout)

        time.sleep(self.STARTUP_TIME)

        if self.server.poll() is not None:
            raise KMIPServerSuicideError(self.server.pid)

        # Set up and open the client proxy; shutdown the server if open fails
        try:
            self.client = KMIPProxy(port=self.KMIP_PORT,
                                    ca_certs=self.CA_CERTS_PATH)
            self.client.open()
        except Exception as e:
            self._shutdown_server()
            raise e

    def tearDown(self):
        super(TestKMIPClientIntegration, self).tearDown()

        # Close the client proxy and shutdown the server
        self.client.close()
        self._shutdown_server()

    def test_create(self):
        result = self._create_symmetric_key()

        self._check_result_status(result.result_status.enum, ResultStatus,
                                  ResultStatus.SUCCESS)
        self._check_object_type(result.object_type.enum, ObjectType,
                                ObjectType.SYMMETRIC_KEY)
        self._check_uuid(result.uuid.value, str)

        # Check the template attribute type
        self._check_template_attribute(result.template_attribute,
                                       TemplateAttribute, 2,
                                       [[str, 'Cryptographic Length', int,
                                         256],
                                        [str, 'Unique Identifier', str,
                                         None]])

    def test_get(self):
        credential_type = CredentialType.USERNAME_AND_PASSWORD
        credential_value = {'Username': 'Peter', 'Password': 'abc123'}
        credential = self.cred_factory.create_credential(credential_type,
                                                         credential_value)
        result = self._create_symmetric_key()
        uuid = result.uuid.value

        result = self.client.get(uuid, credential)

        self._check_result_status(result.result_status.enum, ResultStatus,
                                  ResultStatus.SUCCESS)
        self._check_object_type(result.object_type.enum, ObjectType,
                                ObjectType.SYMMETRIC_KEY)
        self._check_uuid(result.uuid.value, str)

        # Check the secret type
        secret = result.secret

        expected = SymmetricKey
        message = utils.build_er_error(result.__class__, 'type', expected,
                                       secret, 'secret')
        self.assertIsInstance(secret, expected, message)

    def test_destroy(self):
        credential_type = CredentialType.USERNAME_AND_PASSWORD
        credential_value = {'Username': 'Peter', 'Password': 'abc123'}
        credential = self.cred_factory.create_credential(credential_type,
                                                         credential_value)
        result = self._create_symmetric_key()
        uuid = result.uuid.value

        # Verify the secret was created
        result = self.client.get(uuid, credential)

        self._check_result_status(result.result_status.enum, ResultStatus,
                                  ResultStatus.SUCCESS)
        self._check_object_type(result.object_type.enum, ObjectType,
                                ObjectType.SYMMETRIC_KEY)
        self._check_uuid(result.uuid.value, str)

        secret = result.secret

        expected = SymmetricKey
        message = utils.build_er_error(result.__class__, 'type', expected,
                                       secret, 'secret')
        self.assertIsInstance(secret, expected, message)

        # Destroy the SYMMETRIC_KEY object
        result = self.client.destroy(uuid, credential)
        self._check_result_status(result.result_status.enum, ResultStatus,
                                  ResultStatus.SUCCESS)
        self._check_uuid(result.uuid.value, str)

        # Verify the secret was destroyed
        result = self.client.get(uuid, credential)

        self._check_result_status(result.result_status.enum, ResultStatus,
                                  ResultStatus.OPERATION_FAILED)

        expected = ResultReason
        observed = type(result.result_reason.enum)
        message = utils.build_er_error(result.result_reason.__class__, 'type',
                                       expected, observed)
        self.assertEqual(expected, observed, message)

        expected = ResultReason.ITEM_NOT_FOUND
        observed = result.result_reason.enum
        message = utils.build_er_error(result.result_reason.__class__,
                                       'value', expected, observed)
        self.assertEqual(expected, observed, message)

    def test_register(self):
        credential_type = CredentialType.USERNAME_AND_PASSWORD
        credential_value = {'Username': 'Peter', 'Password': 'abc123'}
        credential = self.cred_factory.create_credential(credential_type,
                                                         credential_value)

        object_type = ObjectType.SYMMETRIC_KEY
        algorithm_value = CryptographicAlgorithm.AES
        mask_flags = [CryptographicUsageMask.ENCRYPT,
                      CryptographicUsageMask.DECRYPT]
        attribute_type = AttributeType.CRYPTOGRAPHIC_USAGE_MASK
        usage_mask = self.attr_factory.create_attribute(attribute_type,
                                                        mask_flags)
        attributes = [usage_mask]
        template_attribute = TemplateAttribute(attributes=attributes)

        secret_features = {}

        key_format_type = KeyFormatType.RAW
        secret_features.update([('key_format_type', key_format_type)])

        key_data = {'bytes': bytearray(b'\x00\x00\x00\x00\x00\x00\x00\x00'
                                       b'\x00\x00\x00\x00\x00\x00\x00\x00')}

        secret_features.update([('key_value', key_data)])
        secret_features.update([('cryptographic_algorithm', algorithm_value)])
        secret_features.update([('cryptographic_length', 128)])

        secret = self.secret_factory.create_secret(object_type,
                                                   secret_features)

        result = self.client.register(object_type, template_attribute, secret,
                                      credential)

        self._check_result_status(result.result_status.enum, ResultStatus,
                                  ResultStatus.SUCCESS)
        self._check_uuid(result.uuid.value, str)

        # Check the template attribute type
        self._check_template_attribute(result.template_attribute,
                                       TemplateAttribute, 1,
                                       [[str, 'Unique Identifier', str,
                                         None]])
        # Check that the returned key bytes match what was provided
        uuid = result.uuid.value
        result = self.client.get(uuid, credential)

        self._check_result_status(result.result_status.enum, ResultStatus,
                                  ResultStatus.SUCCESS)
        self._check_object_type(result.object_type.enum, ObjectType,
                                ObjectType.SYMMETRIC_KEY)
        self._check_uuid(result.uuid.value, str)

        # Check the secret type
        secret = result.secret

        expected = SymmetricKey
        message = utils.build_er_error(result.__class__, 'type', expected,
                                       secret, 'secret')
        self.assertIsInstance(secret, expected, message)

        key_block = result.secret.key_block
        key_value = key_block.key_value
        key_material = key_value.key_value.key_material

        expected = key_data.get('bytes')
        observed = key_material.value
        message = utils.build_er_error(key_material.__class__, 'value',
                                       expected, observed, 'value')
        self.assertEqual(expected, observed, message)

    def _shutdown_server(self):
        if self.server.poll() is not None:
            return
        else:
            # Terminate the server process. If it resists, kill it.
            pid = self.server.pid
            self.server.terminate()
            time.sleep(self.SHUTDOWN_TIME)

            if self.server.poll() is None:
                raise KMIPServerZombieError(pid)

    def _create_symmetric_key(self):
        credential_type = CredentialType.USERNAME_AND_PASSWORD
        credential_value = {'Username': 'Peter', 'Password': 'abc123'}
        credential = self.cred_factory.create_credential(credential_type,
                                                         credential_value)

        object_type = ObjectType.SYMMETRIC_KEY
        attribute_type = AttributeType.CRYPTOGRAPHIC_ALGORITHM
        algorithm = self.attr_factory.create_attribute(
            attribute_type,
            CryptographicAlgorithm.AES)

        mask_flags = [CryptographicUsageMask.ENCRYPT,
                      CryptographicUsageMask.DECRYPT]
        attribute_type = AttributeType.CRYPTOGRAPHIC_USAGE_MASK
        usage_mask = self.attr_factory.create_attribute(attribute_type,
                                                        mask_flags)
        attributes = [algorithm, usage_mask]
        template_attribute = TemplateAttribute(attributes=attributes)

        return self.client.create(object_type, template_attribute,
                                  credential)

    def _check_result_status(self, result_status, result_status_type,
                             result_status_value):
        # Error check the result status type and value
        expected = result_status_type
        message = utils.build_er_error(result_status_type, 'type', expected,
                                       result_status)
        self.assertIsInstance(result_status, expected, message)

        expected = result_status_value
        message = utils.build_er_error(result_status_type, 'value', expected,
                                       result_status)
        self.assertEqual(expected, result_status, message)

    def _check_uuid(self, uuid, uuid_type):
        # Error check the UUID type and value
        not_expected = None
        message = utils.build_er_error(uuid_type, 'type',
                                       'not {0}'.format(not_expected), uuid)
        self.assertNotEqual(not_expected, uuid, message)

        expected = uuid_type
        message = utils.build_er_error(uuid_type, 'type', expected, uuid)
        self.assertEqual(expected, type(uuid), message)

    def _check_object_type(self, object_type, object_type_type,
                           object_type_value):
        # Error check the object type type and value
        expected = object_type_type
        message = utils.build_er_error(object_type_type, 'type', expected,
                                       object_type)
        self.assertIsInstance(object_type, expected, message)

        expected = object_type_value
        message = utils.build_er_error(object_type_type, 'value', expected,
                                       object_type)
        self.assertEqual(expected, object_type, message)

    def _check_template_attribute(self, template_attribute,
                                  template_attribute_type, num_attributes,
                                  attribute_features):
        # Error check the template attribute type
        expected = template_attribute_type
        message = utils.build_er_error(template_attribute.__class__, 'type',
                                       expected, template_attribute)
        self.assertIsInstance(template_attribute, expected, message)

        attributes = template_attribute.attributes

        expected = num_attributes
        observed = len(attributes)
        message = utils.build_er_error(TemplateAttribute.__class__, 'number',
                                       expected, observed, 'attributes')

        for i in range(num_attributes):
            features = attribute_features[i]
            self._check_attribute(attributes[i], features[0], features[1],
                                  features[2], features[3])

    def _check_attribute(self, attribute, attribute_name_type,
                         attribute_name_value, attribute_value_type,
                         attribute_value_value):
        # Error check the attribute name and value type and value
        attribute_name = attribute.attribute_name
        attribute_value = attribute.attribute_value

        self._check_attribute_name(attribute_name, attribute_name_type,
                                   attribute_name_value)

        if attribute_name_value == 'Unique Identifier':
            self._check_uuid(attribute_value.value, attribute_value_type)
        else:
            self._check_attribute_value(attribute_value, attribute_value_type,
                                        attribute_value_value)

    def _check_attribute_name(self, attribute_name, attribute_name_type,
                              attribute_name_value):
        # Error check the attribute name type and value
        expected = attribute_name_type
        observed = type(attribute_name.value)
        message = utils.build_er_error(attribute_name_type, 'type', expected,
                                       observed)
        self.assertEqual(expected, observed, message)

        expected = attribute_name_value
        observed = attribute_name.value
        message = utils.build_er_error(attribute_name_type, 'value', expected,
                                       observed)
        self.assertEqual(expected, observed, message)

    def _check_attribute_value(self, attribute_value, attribute_value_type,
                               attribute_value_value):
        expected = attribute_value_type
        observed = type(attribute_value.value)
        message = utils.build_er_error(Attribute, 'type', expected, observed,
                                       'attribute_value')
        self.assertEqual(expected, observed, message)

        expected = attribute_value_value
        observed = attribute_value.value
        message = utils.build_er_error(Attribute, 'value', expected, observed,
                                       'attribute_value')
        self.assertEqual(expected, observed, message)


class TestKMIPClient(TestCase):

    def setUp(self):
        super(TestKMIPClient, self).setUp()

        self.attr_factory = AttributeFactory()
        self.cred_factory = CredentialFactory()
        self.secret_factory = SecretFactory()

        self.client = KMIPProxy()

    def tearDown(self):
        super(TestKMIPClient, self).tearDown()

    # TODO (peter-hamilton) Modify for credential type and/or add new test
    def test_build_credential(self):
        username = 'username'
        password = 'password'
        cred_type = CredentialType.USERNAME_AND_PASSWORD
        self.client.username = username
        self.client.password = password

        credential = self.client._build_credential()

        message = utils.build_er_error(credential.__class__, 'type',
                                       cred_type,
                                       credential.credential_type.enum,
                                       'value')
        self.assertEqual(CredentialType.USERNAME_AND_PASSWORD,
                         credential.credential_type.enum,
                         message)

        message = utils.build_er_error(
            credential.__class__, 'type', username,
            credential.credential_value.username.value, 'value')
        self.assertEqual(username, credential.credential_value.username.value,
                         message)

        message = utils.build_er_error(
            credential.__class__, 'type', password,
            credential.credential_value.password.value, 'value')
        self.assertEqual(password, credential.credential_value.password.value,
                         message)

    def test_build_credential_no_username(self):
        username = None
        password = 'password'
        self.client.username = username
        self.client.password = password

        exception = self.assertRaises(ValueError,
                                      self.client._build_credential)
        self.assertEqual('cannot build credential, username is None',
                         str(exception))

    def test_build_credential_no_password(self):
        username = 'username'
        password = None
        self.client.username = username
        self.client.password = password

        exception = self.assertRaises(ValueError,
                                      self.client._build_credential)
        self.assertEqual('cannot build credential, password is None',
                         str(exception))

    def test_build_credential_no_creds(self):
        self.client.username = None
        self.client.password = None

        credential = self.client._build_credential()

        self.assertEqual(None, credential)

    def _test_build_create_key_pair_batch_item(self, common, private, public):
        batch_item = self.client._build_create_key_pair_batch_item(
            common_template_attribute=common,
            private_key_template_attribute=private,
            public_key_template_attribute=public)

        base = "expected {0}, received {1}"
        msg = base.format(RequestBatchItem, batch_item)
        self.assertIsInstance(batch_item, RequestBatchItem, msg)

        operation = batch_item.operation

        msg = base.format(Operation, operation)
        self.assertIsInstance(operation, Operation, msg)

        operation_enum = operation.enum

        msg = base.format(OperationEnum.CREATE_KEY_PAIR, operation_enum)
        self.assertEqual(OperationEnum.CREATE_KEY_PAIR, operation_enum, msg)

        payload = batch_item.request_payload

        msg = base.format(CreateKeyPairRequestPayload, payload)
        self.assertIsInstance(payload, CreateKeyPairRequestPayload, msg)

        common_observed = payload.common_template_attribute
        private_observed = payload.private_key_template_attribute
        public_observed = payload.public_key_template_attribute

        msg = base.format(common, common_observed)
        self.assertEqual(common, common_observed, msg)

        msg = base.format(private, private_observed)
        self.assertEqual(private, private_observed, msg)

        msg = base.format(public, public_observed)
        self.assertEqual(public, public_observed)

    def test_build_create_key_pair_batch_item_with_input(self):
        self._test_build_create_key_pair_batch_item(
            CommonTemplateAttribute(),
            PrivateKeyTemplateAttribute(),
            PublicKeyTemplateAttribute())

    def test_build_create_key_pair_batch_item_no_input(self):
        self._test_build_create_key_pair_batch_item(None, None, None)

    def test_process_batch_items(self):
        batch_item = ResponseBatchItem(
            operation=Operation(OperationEnum.CREATE_KEY_PAIR),
            response_payload=CreateKeyPairResponsePayload())
        response = ResponseMessage(batch_items=[batch_item, batch_item])
        results = self.client._process_batch_items(response)

        base = "expected {0}, received {1}"
        msg = base.format(list, results)
        self.assertIsInstance(results, list, msg)

        msg = "number of results " + base.format(2, len(results))
        self.assertEqual(2, len(results), msg)

        for result in results:
            msg = base.format(CreateKeyPairResult, result)
            self.assertIsInstance(result, CreateKeyPairResult, msg)

    def test_process_batch_items_no_batch_items(self):
        response = ResponseMessage(batch_items=[])
        results = self.client._process_batch_items(response)

        base = "expected {0}, received {1}"
        msg = base.format(list, results)
        self.assertIsInstance(results, list, msg)

        msg = "number of results " + base.format(0, len(results))
        self.assertEqual(0, len(results), msg)

    def test_process_create_key_pair_batch_item(self):
        batch_item = ResponseBatchItem(
            operation=Operation(OperationEnum.CREATE_KEY_PAIR),
            response_payload=CreateKeyPairResponsePayload())
        result = self.client._process_create_key_pair_batch_item(batch_item)

        msg = "expected {0}, received {1}".format(CreateKeyPairResult, result)
        self.assertIsInstance(result, CreateKeyPairResult, msg)
