# Copyright 2016 IBM Corp. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
A utility class that handles HTTP methods against HMC URIs, based on the
faked HMC.

Note: At this point, the following HTTP methods needed by the zhmcclient
have not been implemented yet::

    POST     /api/partitions/([^/]+)/operations/scsi-dump
    POST     /api/partitions/([^/]+)/operations/psw-restart
    POST     /api/partitions/([^/]+)/operations/mount-iso-image
    POST     /api/partitions/([^/]+)/operations/unmount-iso-image
    POST     /api/partitions/([^/]+)/hbas/([^/]+)/operations/reassign-
               storage-adapter-port
    POST     /api/virtual-switches/([^/]+)/operations/get-connected-vnics
    POST     /api/cpcs/([^/]+)/operations/import-profiles
    POST     /api/cpcs/([^/]+)/operations/export-profiles
"""

from __future__ import absolute_import

import re

__all__ = ['UriHandler', 'HTTPError', 'URIS']


class HTTPError(Exception):

    def __init__(self, method, uri, http_status, reason, message):
        self.method = method
        self.uri = uri
        self.http_status = http_status
        self.reason = reason
        self.message = message

    def response(self):
        return {
            'request-method': self.method,
            'request-uri': self.uri,
            'http-status': self.http_status,
            'reason': self.reason,
            'message': self.message,
        }


class InvalidResourceError(HTTPError):

    def __init__(self, method, uri, handler_class=None):
        if handler_class is not None:
            handler_txt = "handler class %s" % handler_class.__name__
        else:
            handler_txt = "no handler class"
        super(InvalidResourceError, self).__init__(
            method, uri,
            http_status=404,
            reason=1,
            message="Unknown resource with URI: %s (%s)" % (uri, handler_txt))


class InvalidMethodError(HTTPError):

    def __init__(self, method, uri, handler_class=None):
        if handler_class is not None:
            handler_txt = "handler class %s" % handler_class.__name__
        else:
            handler_txt = "no handler class"
        super(InvalidMethodError, self).__init__(
            method, uri,
            http_status=404,
            reason=1,
            message="Invalid HTTP method %s on URI: %s %s" %
            (method, uri, handler_txt))


class CpcNotInDpmError(HTTPError):

    def __init__(self, method, uri, cpc):
        super(CpcNotInDpmError, self).__init__(
            method, uri,
            http_status=409,
            reason=5,
            message="CPC is not in DPM mode: %s" % cpc.uri)


class CpcInDpmError(HTTPError):

    def __init__(self, method, uri, cpc):
        super(CpcInDpmError, self).__init__(
            method, uri,
            http_status=409,
            reason=4,
            message="CPC is in DPM mode: %s" % cpc.uri)


class UriHandler(object):
    """
    Handle HTTP methods against a set of known URIs and invoke respective
    handlers.
    """

    def __init__(self, uris):
        self._uri_handlers = []  # tuple of (regexp-pattern, handler-name)
        for uri, handler_class in uris:
            uri_pattern = re.compile('^' + uri + '$')
            tup = (uri_pattern, handler_class)
            self._uri_handlers.append(tup)

    def handler(self, uri, method):
        for uri_pattern, handler_class in self._uri_handlers:
            m = uri_pattern.match(uri)
            if m:
                uri_parms = m.groups()
                return handler_class, uri_parms
        raise InvalidResourceError(method, uri)

    def get(self, hmc, uri, logon_required):
        handler_class, uri_parms = self.handler(uri, 'GET')
        if not getattr(handler_class, 'get', None):
            raise InvalidMethodError('GET', uri, handler_class)
        return handler_class.get(hmc, uri, uri_parms, logon_required)

    def post(self, hmc, uri, body, logon_required, wait_for_completion):
        handler_class, uri_parms = self.handler(uri, 'POST')
        if not getattr(handler_class, 'post', None):
            raise InvalidMethodError('POST', uri, handler_class)
        return handler_class.post(hmc, uri, uri_parms, body, logon_required,
                                  wait_for_completion)

    def delete(self, hmc, uri, logon_required):
        handler_class, uri_parms = self.handler(uri, 'DELETE')
        if not getattr(handler_class, 'delete', None):
            raise InvalidMethodError('DELETE', uri, handler_class)
        handler_class.delete(hmc, uri, uri_parms, logon_required)


class GenericGetPropertiesHandler(object):

    @staticmethod
    def get(hmc, uri, uri_parms, logon_required):
        """Operation: Get <resource> Properties."""
        try:
            resource = hmc.lookup_by_uri(uri)
        except KeyError:
            raise InvalidResourceError('GET', uri)
        return resource.properties


class GenericUpdatePropertiesHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Update <resource> Properties."""
        assert wait_for_completion is True  # async not supported yet
        try:
            resource = hmc.lookup_by_uri(uri)
        except KeyError:
            raise InvalidResourceError('GET', uri)
        resource.update(body)


class VersionHandler(object):

    @staticmethod
    def get(hmc, uri, uri_parms, logon_required):
        api_major, api_minor = hmc.api_version.split('.')
        return {
            'hmc-name': hmc.hmc_name,
            'hmc-version': hmc.hmc_version,
            'api-major-version': int(api_major),
            'api-minor-version': int(api_minor),
        }


class CpcsHandler(object):

    @staticmethod
    def get(hmc, uri, uri_parms, logon_required):
        """Operation: List CPCs."""
        result_cpcs = []
        for cpc in hmc.cpcs.list():
            result_cpc = {}
            for prop in cpc.properties:
                if prop in ('object-uri', 'name', 'status'):
                    result_cpc[prop] = cpc.properties[prop]
            result_cpcs.append(result_cpc)
        return {'cpcs': result_cpcs}


class CpcHandler(GenericGetPropertiesHandler,
                 GenericUpdatePropertiesHandler):
    pass


class CpcStartHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Start CPC (requires DPM mode)."""
        assert wait_for_completion is True  # async not supported yet
        cpc_oid = uri_parms[0]
        try:
            cpc = hmc.cpcs.lookup_by_oid(cpc_oid)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('POST', uri, cpc)
        cpc.properties['status'] = 'active'


class CpcStopHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Stop CPC (requires DPM mode)."""
        assert wait_for_completion is True  # async not supported yet
        cpc_oid = uri_parms[0]
        try:
            cpc = hmc.cpcs.lookup_by_oid(cpc_oid)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('POST', uri, cpc)
        cpc.properties['status'] = 'not-operating'


class CpcExportPortNamesListHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Export WWPN List (requires DPM mode)."""
        assert wait_for_completion is True  # this operation is always synchr.
        cpc_oid = uri_parms[0]
        try:
            cpc = hmc.cpcs.lookup_by_oid(cpc_oid)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('POST', uri, cpc)

        if body is None or 'partitions' not in body:
            raise HTTPError('POST', uri, 400,
                            149,  # TODO: Maybe use different reason?
                            "No 'partitions' property provided in request "
                            "body.")
        partition_uris = body['partitions']
        if len(partition_uris) == 0:
            raise HTTPError('POST', uri, 400, 149,
                            "'partitions' property provided in request "
                            "body is empty.")

        wwpn_list = []
        for partition_uri in partition_uris:
            partition = hmc.lookup_by_uri(partition_uri)
            partition_cpc = partition.manager.parent
            if partition_cpc.oid != cpc_oid:
                raise HTTPError('POST', uri, 400,
                                149,  # TODO: Maybe use different reason?
                                "Partition with object ID %s specified in "
                                "'partitions' property is not in CPC with "
                                "object ID %s." % (partition.oid, cpc_oid))
            partition_name = partition.properties.get('name', '')
            for hba in partition.hbas.list():
                port_uri = hba.properties['adapter-port-uri']
                port = hmc.lookup_by_uri(port_uri)
                adapter = port.manager.parent
                devno = hba.properties.get('device-number', '')
                wwpn = hba.properties.get('wwpn', '')
                wwpn_str = '%s,%s,%s,%s' % (partition_name, adapter.oid,
                                            devno, wwpn)
                wwpn_list.append(wwpn_str)
        return {
            'wwpn-list': wwpn_list
        }


class AdaptersHandler(object):

    @staticmethod
    def get(hmc, uri, uri_parms, logon_required):
        """Operation: List Adapters of a CPC."""
        cpc_oid = uri_parms[0]
        try:
            cpc = hmc.cpcs.lookup_by_oid(cpc_oid)
        except KeyError:
            raise InvalidResourceError('GET', uri)
        if not cpc.dpm_enabled:
            raise InvalidResourceError('GET', uri)  # in List: not found
        result_adapters = []
        for adapter in cpc.adapters.list():
            result_adapter = {}
            for prop in adapter.properties:
                if prop in ('object-uri', 'name', 'status'):
                    result_adapter[prop] = adapter.properties[prop]
            result_adapters.append(result_adapter)
        return {'adapters': result_adapters}


class AdapterHandler(GenericGetPropertiesHandler,
                     GenericUpdatePropertiesHandler):
    pass


class NetworkPortHandler(GenericGetPropertiesHandler,
                         GenericUpdatePropertiesHandler):
    pass


class StoragePortHandler(GenericGetPropertiesHandler,
                         GenericUpdatePropertiesHandler):
    pass


class PartitionsHandler(object):

    @staticmethod
    def get(hmc, uri, uri_parms, logon_required):
        """Operation: List Partitions of a CPC."""
        cpc_oid = uri_parms[0]
        try:
            cpc = hmc.cpcs.lookup_by_oid(cpc_oid)
        except KeyError:
            raise InvalidResourceError('GET', uri)
        if not cpc.dpm_enabled:
            raise InvalidResourceError('GET', uri)  # in List: not found
        result_partitions = []
        for partition in cpc.partitions.list():
            result_partition = {}
            for prop in partition.properties:
                if prop in ('object-uri', 'name', 'status'):
                    result_partition[prop] = partition.properties[prop]
            result_partitions.append(result_partition)
        return {'partitions': result_partitions}

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Create Partition."""
        assert wait_for_completion is True  # async not supported yet
        cpc_oid = uri_parms[0]
        try:
            cpc = hmc.cpcs.lookup_by_oid(cpc_oid)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('POST', uri, cpc)
        new_partition = cpc.partitions.add(body)
        return {'object-uri': new_partition.uri}


class PartitionHandler(GenericGetPropertiesHandler,
                       GenericUpdatePropertiesHandler):

    @staticmethod
    def delete(hmc, uri, uri_parms, logon_required):
        """Operation: Delete Partition."""
        try:
            partition = hmc.lookup_by_uri(uri)
        except KeyError:
            raise InvalidResourceError('DELETE', uri)
        cpc = partition.manager.parent
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('DELETE', uri, cpc)
        partition.manager.remove(partition.oid)


class PartitionStartHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Start Partition (requires DPM mode)."""
        assert wait_for_completion is True  # async not supported yet
        partition_uri = uri.split('/operations/')[0]
        try:
            partition = hmc.lookup_by_uri(partition_uri)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        cpc = partition.manager.parent
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('POST', uri, cpc)
        partition.properties['status'] = 'active'


class PartitionStopHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Start Partition (requires DPM mode)."""
        assert wait_for_completion is True  # async not supported yet
        partition_uri = uri.split('/operations/')[0]
        try:
            partition = hmc.lookup_by_uri(partition_uri)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        cpc = partition.manager.parent
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('POST', uri, cpc)
        partition.properties['status'] = 'stopped'


class HbasHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Create HBA."""
        assert wait_for_completion is True  # async not supported yet
        partition_uri = re.sub('/hbas$', '', uri)
        try:
            partition = hmc.lookup_by_uri(partition_uri)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        cpc = partition.manager.parent
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('POST', uri, cpc)
        new_hba = partition.hbas.add(body)
        return {'element-uri': new_hba.uri}


class HbaHandler(GenericGetPropertiesHandler,
                 GenericUpdatePropertiesHandler):

    @staticmethod
    def delete(hmc, uri, uri_parms, logon_required):
        """Operation: Delete HBA."""
        try:
            hba = hmc.lookup_by_uri(uri)
        except KeyError:
            raise InvalidResourceError('DELETE', uri)
        partition = hba.manager.parent
        cpc = partition.manager.parent
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('DELETE', uri, cpc)
        partition.hbas.remove(hba.oid)


class NicsHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Create NIC."""
        assert wait_for_completion is True  # async not supported yet
        partition_uri = re.sub('/nics$', '', uri)
        try:
            partition = hmc.lookup_by_uri(partition_uri)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        cpc = partition.manager.parent
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('POST', uri, cpc)
        new_nic = partition.nics.add(body)
        return {'element-uri': new_nic.uri}


class NicHandler(GenericGetPropertiesHandler,
                 GenericUpdatePropertiesHandler):

    @staticmethod
    def delete(hmc, uri, uri_parms, logon_required):
        """Operation: Delete NIC."""
        try:
            nic = hmc.lookup_by_uri(uri)
        except KeyError:
            raise InvalidResourceError('DELETE', uri)
        partition = nic.manager.parent
        cpc = partition.manager.parent
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('DELETE', uri, cpc)
        partition.nics.remove(nic.oid)


class VirtualFunctionsHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Create Virtual Function"""
        assert wait_for_completion is True  # async not supported yet
        partition_uri = re.sub('/virtual-functions$', '', uri)
        try:
            partition = hmc.lookup_by_uri(partition_uri)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        cpc = partition.manager.parent
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('POST', uri, cpc)
        new_vf = partition.virtual_functions.add(body)
        return {'element-uri': new_vf.uri}


class VirtualFunctionHandler(GenericGetPropertiesHandler,
                             GenericUpdatePropertiesHandler):

    @staticmethod
    def delete(hmc, uri, uri_parms, logon_required):
        """Operation: Delete Virtual Function."""
        try:
            vf = hmc.lookup_by_uri(uri)
        except KeyError:
            raise InvalidResourceError('DELETE', uri)
        partition = vf.manager.parent
        cpc = partition.manager.parent
        if not cpc.dpm_enabled:
            raise CpcNotInDpmError('DELETE', uri, cpc)
        partition.virtual_functions.remove(vf.oid)


class VirtualSwitchesHandler(object):

    @staticmethod
    def get(hmc, uri, uri_parms, logon_required):
        """Operation: List Virtual Switches of a CPC."""
        cpc_oid = uri_parms[0]
        try:
            cpc = hmc.cpcs.lookup_by_oid(cpc_oid)
        except KeyError:
            raise InvalidResourceError('GET', uri)
        if not cpc.dpm_enabled:
            raise InvalidResourceError('GET', uri)  # in List: not found
        result_vswitches = []
        for vswitch in cpc.virtual_switches.list():
            result_vswitch = {}
            for prop in vswitch.properties:
                if prop in ('object-uri', 'name', 'type'):
                    result_vswitch[prop] = vswitch.properties[prop]
            result_vswitches.append(result_vswitch)
        return {'virtual-switches': result_vswitches}


class VirtualSwitchHandler(GenericGetPropertiesHandler,
                           GenericUpdatePropertiesHandler):
    pass


class LparsHandler(object):

    @staticmethod
    def get(hmc, uri, uri_parms, logon_required):
        """Operation: List Logical Partitions of CPC."""
        cpc_oid = uri_parms[0]
        try:
            cpc = hmc.cpcs.lookup_by_oid(cpc_oid)
        except KeyError:
            raise InvalidResourceError('GET', uri)
        if cpc.dpm_enabled:
            raise InvalidResourceError('GET', uri)  # in List: not found
        result_lpars = []
        for lpar in cpc.lpars.list():
            result_lpar = {}
            for prop in lpar.properties:
                if prop in ('object-uri', 'name', 'status'):
                    result_lpar[prop] = lpar.properties[prop]
            result_lpars.append(result_lpar)
        return {'logical-partitions': result_lpars}


class LparHandler(GenericGetPropertiesHandler,
                  GenericUpdatePropertiesHandler):
    pass


class LparActivateHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Activate Logical Partition (requires classic mode)."""
        assert wait_for_completion is True  # async not supported yet
        lpar_uri = uri.split('/operations/')[0]
        try:
            lpar = hmc.lookup_by_uri(lpar_uri)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        cpc = lpar.manager.parent
        if cpc.dpm_enabled:
            raise CpcInDpmError('POST', uri, cpc)
        lpar.properties['status'] = 'not-operating'


class LparDeactivateHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Deactivate Logical Partition (requires classic mode)."""
        assert wait_for_completion is True  # async not supported yet
        lpar_uri = uri.split('/operations/')[0]
        try:
            lpar = hmc.lookup_by_uri(lpar_uri)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        cpc = lpar.manager.parent
        if cpc.dpm_enabled:
            raise CpcInDpmError('POST', uri, cpc)
        lpar.properties['status'] = 'not-activated'


class LparLoadHandler(object):

    @staticmethod
    def post(hmc, uri, uri_parms, body, logon_required, wait_for_completion):
        """Operation: Load Logical Partition (requires classic mode)."""
        assert wait_for_completion is True  # async not supported yet
        lpar_uri = uri.split('/operations/')[0]
        try:
            lpar = hmc.lookup_by_uri(lpar_uri)
        except KeyError:
            raise InvalidResourceError('POST', uri)
        cpc = lpar.manager.parent
        if cpc.dpm_enabled:
            raise CpcInDpmError('POST', uri, cpc)
        lpar.properties['status'] = 'operating'


class ResetActProfilesHandler(object):

    @staticmethod
    def get(hmc, uri, uri_parms, logon_required):
        """Operation: List Reset Activation Profiles."""
        cpc_oid = uri_parms[0]
        try:
            cpc = hmc.cpcs.lookup_by_oid(cpc_oid)
        except KeyError:
            raise InvalidResourceError('GET', uri)
        if cpc.dpm_enabled:
            raise InvalidResourceError('GET', uri)  # in List: not found
        result_profiles = []
        for profile in cpc.reset_activation_profiles.list():
            result_profile = {}
            for prop in profile.properties:
                if prop in ('element-uri', 'name'):
                    result_profile[prop] = profile.properties[prop]
            result_profiles.append(result_profile)
        return {'reset-activation-profiles': result_profiles}


class ResetActProfileHandler(GenericGetPropertiesHandler,
                             GenericUpdatePropertiesHandler):
    pass


class ImageActProfilesHandler(object):

    @staticmethod
    def get(hmc, uri, uri_parms, logon_required):
        """Operation: List Image Activation Profiles."""
        cpc_oid = uri_parms[0]
        try:
            cpc = hmc.cpcs.lookup_by_oid(cpc_oid)
        except KeyError:
            raise InvalidResourceError('GET', uri)
        if cpc.dpm_enabled:
            raise InvalidResourceError('GET', uri)  # in List: not found
        result_profiles = []
        for profile in cpc.image_activation_profiles.list():
            result_profile = {}
            for prop in profile.properties:
                if prop in ('element-uri', 'name'):
                    result_profile[prop] = profile.properties[prop]
            result_profiles.append(result_profile)
        return {'image-activation-profiles': result_profiles}


class ImageActProfileHandler(GenericGetPropertiesHandler,
                             GenericUpdatePropertiesHandler):
    pass


class LoadActProfilesHandler(object):

    @staticmethod
    def get(hmc, uri, uri_parms, logon_required):
        """Operation: List Load Activation Profiles."""
        cpc_oid = uri_parms[0]
        try:
            cpc = hmc.cpcs.lookup_by_oid(cpc_oid)
        except KeyError:
            raise InvalidResourceError('GET', uri)
        if cpc.dpm_enabled:
            raise InvalidResourceError('GET', uri)  # in List: not found
        result_profiles = []
        for profile in cpc.load_activation_profiles.list():
            result_profile = {}
            for prop in profile.properties:
                if prop in ('element-uri', 'name'):
                    result_profile[prop] = profile.properties[prop]
            result_profiles.append(result_profile)
        return {'load-activation-profiles': result_profiles}


class LoadActProfileHandler(GenericGetPropertiesHandler,
                            GenericUpdatePropertiesHandler):
    pass


# URIs to be handled
URIS = (

    # In all modes:

    ('/api/version', VersionHandler),

    ('/api/cpcs', CpcsHandler),
    ('/api/cpcs/([^/]+)', CpcHandler),

    # Only in DPM mode:

    ('/api/cpcs/([^/]+)/operations/start', CpcStartHandler),
    ('/api/cpcs/([^/]+)/operations/stop', CpcStopHandler),
    ('/api/cpcs/([^/]+)/operations/export-port-names-list',
     CpcExportPortNamesListHandler),

    ('/api/cpcs/([^/]+)/adapters', AdaptersHandler),
    ('/api/adapters/([^/]+)', AdapterHandler),

    ('/api/adapters/([^/]+)/network-ports/([^/]+)', NetworkPortHandler),

    ('/api/adapters/([^/]+)/storage-ports/([^/]+)', StoragePortHandler),

    ('/api/cpcs/([^/]+)/partitions', PartitionsHandler),
    ('/api/partitions/([^/]+)', PartitionHandler),
    ('/api/partitions/([^/]+)/operations/start', PartitionStartHandler),
    ('/api/partitions/([^/]+)/operations/stop', PartitionStopHandler),
    # ('/api/partitions/([^/]+)/operations/scsi-dump',
    #  PartitionScsiDumpHandler),
    # ('/api/partitions/([^/]+)/operations/psw-restart',
    #  PartitionPswRestartHandler),
    # ('/api/partitions/([^/]+)/operations/mount-iso-image',
    #  PartitionMountIsoImageHandler),
    # ('/api/partitions/([^/]+)/operations/unmount-iso-image',
    #  PartitionUnmountIsoImageHandler),

    ('/api/partitions/([^/]+)/hbas', HbasHandler),
    ('/api/partitions/([^/]+)/hbas/([^/]+)', HbaHandler),
    # ('/api/partitions/([^/]+)/hbas/([^/]+)/operations/'\
    #  'reassign-storage-adapter-port', HbaReassignPortHandler),

    ('/api/partitions/([^/]+)/nics', NicsHandler),
    ('/api/partitions/([^/]+)/nics/([^/]+)', NicHandler),

    ('/api/partitions/([^/]+)/virtual-functions', VirtualFunctionsHandler),
    ('/api/partitions/([^/]+)/virtual-functions/([^/]+)',
     VirtualFunctionHandler),

    ('/api/cpcs/([^/]+)/virtual-switches', VirtualSwitchesHandler),
    ('/api/virtual-switches/([^/]+)', VirtualSwitchHandler),
    # ('/api/virtual-switches/([^/]+)/operations/get-connected-vnics',
    #  VirtualSwitchGetVnicsHandler),

    # Only in classic (or ensemble) mode:

    # ('/api/cpcs/([^/]+)/operations/import-profiles',
    #  CpcImportProfilesHandler),
    # ('/api/cpcs/([^/]+)/operations/export-profiles',
    #  CpcExportProfilesHandler),

    ('/api/cpcs/([^/]+)/logical-partitions', LparsHandler),
    ('/api/logical-partitions/([^/]+)', LparHandler),
    ('/api/logical-partitions/([^/]+)/operations/activate',
     LparActivateHandler),
    ('/api/logical-partitions/([^/]+)/operations/deactivate',
     LparDeactivateHandler),
    ('/api/logical-partitions/([^/]+)/operations/load', LparLoadHandler),

    ('/api/cpcs/([^/]+)/reset-activation-profiles', ResetActProfilesHandler),
    ('/api/cpcs/([^/]+)/reset-activation-profiles/([^/]+)',
     ResetActProfileHandler),

    ('/api/cpcs/([^/]+)/image-activation-profiles', ImageActProfilesHandler),
    ('/api/cpcs/([^/]+)/image-activation-profiles/([^/]+)',
     ImageActProfileHandler),

    ('/api/cpcs/([^/]+)/load-activation-profiles', LoadActProfilesHandler),
    ('/api/cpcs/([^/]+)/load-activation-profiles/([^/]+)',
     LoadActProfileHandler),
)