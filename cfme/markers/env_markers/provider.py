from collections import defaultdict
from distutils.version import LooseVersion

import pytest
import yaml

from cfme.markers.env import EnvironmentMarker
from cfme.utils.log import logger
from cfme.utils.providers import ProviderFilter, list_providers, all_types
from cfme.utils.pytest_shortcuts import fixture_filter
from cfme.utils.version import Version

ONE = 'one'
ALL = 'all'
LATEST = 'latest'
ONE_PER_VERSION = 'one_per_version'
ONE_PER_CATEGORY = 'one_per_category'
ONE_PER_TYPE = 'one_per_type'


class DPFilter(ProviderFilter):
    def __call__(self, provider):
        """ Applies this filter on a given DataProvider

        Usage:
            pf = ProviderFilter('cloud_infra', categories=['cloud', 'infra'])
            providers = list_providers([pf])
            pf2 = ProviderFilter(
                classes=[GCEProvider, EC2Provider], required_fields=['small_template'])
            provider_keys = [prov.key for prov in list_providers([pf, pf2])]
            ^ this will list keys of all GCE and EC2 providers
            ...or...
            pf = ProviderFilter(required_tags=['openstack', 'complete'])
            pf_inverted = ProviderFilter(required_tags=['disabled'], inverted=True)
            providers = list_providers([pf, pf_inverted])
            ^ this will return providers that have both the "openstack" and "complete" tags set
              and at the same time don't have the "disabled" tag
            ...or...
            pf = ProviderFilter(keys=['rhevm34'], class=CloudProvider, conjunctive=False)
            providers = list_providers([pf])
            ^ this will list all providers that either have the 'rhevm34' key or are an instance
              of the CloudProvider class and therefore are a cloud provider

        Returns:
            `True` if provider passed all checks and was not filtered out, `False` otherwise.
            The result is opposite if the 'inverted' attribute is set to `True`.
        """
        classes_l = self._filter_classes(provider)
        results = [classes_l]
        relevant_results = [res for res in results if res in [True, False]]
        compiling_fn = all if self.conjunctive else any
        # If all / any filters return true, the provider was not blocked (unless inverted)
        if compiling_fn(relevant_results):
            return not self.inverted
        return self.inverted

    def _filter_classes(self, provider):
        """ Filters by provider (base) classes """
        if self.classes is None:
            return None
        return any([prov_class in all_types()[provider.type_name].__mro__ for prov_class in self.classes])


def _param_check(metafunc, argnames, argvalues):
    """Helper function to check if parametrizing is necessary

    * If no argnames were specified, parametrization is unnecessary.
    * If argvalues were generated, parametrization is necessary.
    * If argnames were specified, but no values were generated, the test cannot run successfully,
      and will be uncollected using the :py:mod:`markers.uncollect` mark.

    See usage in :py:func:`parametrize`

    Args:
        metafunc: metafunc objects from pytest_generate_tests
        argnames: argnames list for use in metafunc.parametrize
        argvalues: argvalues list for use in metafunc.parametrize

    Returns:
        * ``True`` if this test should be parametrized
        * ``False`` if it shouldn't be parametrized
        * ``None`` if the test will be uncollected

    """
    # If no parametrized args were named, don't parametrize
    if not argnames:
        return False
    # If parametrized args were named and values were generated, parametrize
    elif any(argvalues):
        return True
    # If parametrized args were named, but no values were generated, mark this test to be
    # removed from the test collection. Otherwise, py.test will try to find values for the
    # items in argnames by looking in its fixture pool, which will almost certainly fail.
    else:
        # module and class are optional, but function isn't
        modname = getattr(metafunc.module, '__name__', None)
        classname = getattr(metafunc.cls, '__name__', None)
        funcname = metafunc.function.__name__

        test_name = '.'.join(filter(None, (modname, classname, funcname)))
        uncollect_msg = 'Parametrization for {} yielded no values,'\
            ' marked for uncollection'.format(test_name)
        logger.warning(uncollect_msg)

        # apply the mark
        pytest.mark.uncollect(reason=uncollect_msg)(metafunc.function)


def parametrize(metafunc, argnames, argvalues, *args, **kwargs):
    """parametrize wrapper that calls :py:func:`_param_check`, and only parametrizes when needed

    This can be used in any place where conditional parametrization is used.

    """
    kwargs.pop('selector')
    if _param_check(metafunc, argnames, argvalues):
        metafunc.parametrize(argnames, argvalues, *args, **kwargs)
    # if param check failed and the test was supposed to be parametrized around a provider
    elif 'provider' in metafunc.fixturenames:
        try:
            # hack to pass trough in case of a failed param_check
            # where it sets a custom message
            metafunc.function.uncollect
        except AttributeError:
            pytest.mark.uncollect(
                reason="provider was not parametrized did you forget --use-provider?"
            )(metafunc.function)


class DataProvider(object):
    """A simple holder for a pseudo provider.

    This is not a real provider. This is used in place of a real provider to allow things like
    uncollections to take place in the case that we do not actually have an environment set up
    for this particular provider.
    """
    def __init__(self, category, type, version):
        self.category = category
        self.type_name = type
        self.version = version
        self.klass = all_types()[self.type_name]

    def one_of(self, *classes):
        return any([klass in self.klass.__mro__ for klass in classes])

    def __repr__(self):
        return '{}({})[{}]'.format(self.type_name, self.category, self.version)


def all_required(miq_version, filters=None):
    """This returns a list DataProvider objects

    This list of providers is a representative of the providers that a test should be run against.

    Args:
        miq_version: The version of miq to query the supportability
        filters: A list of filters
    """
    # Load the supportability YAML and extrace the providers portion
    stream = Version(miq_version).series()
    with open('supportability.yaml') as f:
        data = yaml.load(f)
    data_for_stream = data[stream]['providers']

    # Build up a list of tuples in the form of category, type dictionary,
    #  [('cloud', {'openstack': [8, 9, 10]}), ('cloud', {'ec2'})]
    types = [
        (cat, type)
        for cat, types in data_for_stream.items()
        for type in types
    ]

    # Build up a list of data providers by iterating the types list from above
    dprovs = []
    for cat, prov_type_or_dict in types:
        if isinstance(prov_type_or_dict, basestring):
            # If the provider is versionless, ie, EC2, GCE, set the version number to 0
            dprovs.append(DataProvider(cat, prov_type_or_dict, 0))
        else:
            # If the prov_type_or_dict is not just a string, then we have versions and need
            # to iterate and extend the list
            dprovs.extend([
                DataProvider(cat, prov, ver)
                for prov, vers in prov_type_or_dict.items()
                for ver in vers
            ])

    nfilters = [DPFilter(classes=f.classes) for f in filters if isinstance(f, ProviderFilter)]
    for prov_filter in nfilters:
        dprovs = filter(prov_filter, dprovs)
    return dprovs


def providers(metafunc, filters=None, selector=ALL):
    """ Gets providers based on given (+ global) filters

    Note:
        Using the default 'function' scope, each test will be run individually for each provider
        before moving on to the next test. To group all tests related to single provider together,
        parametrize tests in the 'module' scope.

    Note:
        testgen for providers now requires the usage of test_flags for collection to work.
        Please visit http://cfme-tests.readthedocs.org/guides/documenting.html#documenting-tests
        for more details.
    """
    filters = filters or []
    argnames = []
    argvalues = []
    idlist = []

    # Obtains the test's flags in form of a ProviderFilter
    meta = getattr(metafunc.function, 'meta', None)
    test_flag_str = getattr(meta, 'kwargs', {}).get('from_docs', {}).get('test_flag')
    if test_flag_str:
        test_flags = test_flag_str.split(',')
        flags_filter = ProviderFilter(required_flags=test_flags)
        filters = filters + [flags_filter]

    # available_providers are the ones "available" from the yamls after all of the global and
    # local filters have been applied. It will be a list of crud objects.
    available_providers = list_providers(filters)

    # supported_providers are the ones "supported" in the supportability.yaml file. It will
    # be a list of DataProvider objects and will be filtered based upon what the test has asked for
    supported_providers = all_required('5.9', filters)

    # Now we run through the selectors and build up a list of supported providers which match our
    # requirements. This then forms the providers that the test should run against.
    if selector == ONE:
        if supported_providers:
            allowed_providers = [supported_providers[0]]
        else:
            allowed_providers = []
    elif selector == LATEST:
        allowed_providers = [sorted(
            supported_providers, key=lambda k:LooseVersion(
                str(k.version)), reverse=True
        )[0]]
    elif selector == ONE_PER_TYPE:
        types = set()

        def add_prov(prov):
            types.add(prov.type_name)
            return prov

        allowed_providers = [
            add_prov(prov) for prov in supported_providers if prov.type_name not in types
        ]
    elif selector == ONE_PER_CATEGORY:
        categories = set()

        def add_prov(prov):
            categories.add(prov.category)
            return prov

        allowed_providers = [
            add_prov(prov) for prov in supported_providers if prov.category not in categories
        ]
    elif selector == ONE_PER_VERSION:
        # This needs to handle versions per type
        versions = defaultdict(set)

        def add_prov(prov):
            versions[prov.type_name].add(prov.version)
            return prov

        allowed_providers = [
            add_prov(prov)
            for prov in supported_providers
            if prov.version not in versions[prov.type_name]
        ]
    else:
        # If there are no selectors, then the allowed providers are whichever are supported
        allowed_providers = supported_providers

    # This list will now tell us exactly which providers should be run for this test
    required_providers = allowed_providers

    def get_valid_provider(provider):
        # We now search theough all the available providers looking for one that matches the
        # criteria. If we don't find one, we return None
        for a_prov in available_providers:
            try:
                assert a_prov.version
                if a_prov.version == provider.version and \
                        a_prov.type == provider.type_name and \
                        a_prov.category == provider.category:
                    return a_prov
            except (AssertionError, KeyError):
                if a_prov.type == provider.type_name and \
                        a_prov.category == provider.category:
                    return a_prov
        else:
            return None

    # A small routine to check if we need to supply the idlist a provider type or
    # a real type/version
    need_prov_keys = False
    for filter in filters:
        if isinstance(filter, ProviderFilter):
            for filt in filter.classes:
                if hasattr(filt, 'type_name'):
                    need_prov_keys = True
                    break

    # Now we iterate through the required providers and try to match them to the available ones
    for provider in required_providers:
        the_prov = get_valid_provider(provider)

        # If no provider is sound then we append the DataProvider, and a skip mark. This means
        # that the environment didn't have that particular provider. Boo hoo!
        if the_prov:
            argvalues.append(pytest.param(the_prov))
        else:
            argvalues.append(
                pytest.param(
                    provider, marks=pytest.mark.skip("Environment for this provider, not available")
                )
            )

        # Use the provider key for idlist, helps with readable parametrized test output
        # TODO: handle EC2
        ver = provider.version if provider.version else None
        if ver:
            the_id = "{}-{}".format(provider.type_name, provider.version)
        else:
            the_id = "{}".format(provider.type_name)

        # Now we modify the id based on what selector we chose
        if selector == ONE:
            if need_prov_keys:
                idlist.append(provider.type_name)
            else:
                idlist.append(provider.category)
        elif selector == ONE_PER_CATEGORY:
            idlist.append(provider.category)
        elif selector == ONE_PER_TYPE:
            idlist.append(provider.type_name)
        else:
            idlist.append(the_id)

        # Add provider to argnames if missing
        if 'provider' in metafunc.fixturenames and 'provider' not in argnames:
            metafunc.function = pytest.mark.uses_testgen()(metafunc.function)
            argnames.append('provider')
        if metafunc.config.getoption('sauce') or selector == ONE:
            break
    return argnames, argvalues, idlist


def providers_by_class(metafunc, classes, required_fields=None, selector=ALL):
    """ Gets providers by their class

    Args:
        metafunc: Passed in by pytest
        classes: List of classes to fetch
        required_fields: See :py:class:`cfme.utils.provider.ProviderFilter`

    Usage:
        # In the function itself
        def pytest_generate_tests(metafunc):
            argnames, argvalues, idlist = testgen.providers_by_class(
                [GCEProvider, AzureProvider], required_fields=['provisioning']
            )
        metafunc.parametrize(argnames, argvalues, ids=idlist, scope='module')

        # Using the parametrize wrapper
        pytest_generate_tests = testgen.parametrize([GCEProvider], scope='module')
    """
    pf = ProviderFilter(classes=classes, required_fields=required_fields)
    return providers(metafunc, filters=[pf], selector=selector)


class ProviderEnvironmentMarker(EnvironmentMarker):
    NAME = 'provider'

    def process_env_mark(self, metafunc):
        if hasattr(metafunc.function, self.NAME):
            args = getattr(metafunc.function, self.NAME).args
            kwargs = getattr(metafunc.function, self.NAME).kwargs.copy()

            scope = kwargs.pop('scope', 'function')
            indirect = kwargs.pop('indirect', False)
            filter_unused = kwargs.pop('filter_unused', True)
            selector = kwargs.pop('selector', ALL)
            gen_func = kwargs.pop('gen_func', providers_by_class)

            # If parametrize doesn't get you what you need, steal this and modify as needed
            kwargs.update({'selector': selector})
            argnames, argvalues, idlist = gen_func(metafunc, *args, **kwargs)
            # Filter out argnames that aren't requested on the metafunc test item, so not all tests
            # need all fixtures to run, and tests not using gen_func's fixtures aren't parametrized.
            if filter_unused:
                argnames, argvalues = fixture_filter(metafunc, argnames, argvalues)
                # See if we have to parametrize at all after filtering
            parametrize(
                metafunc, argnames, argvalues, indirect=indirect,
                ids=idlist, scope=scope, selector=selector
            )
