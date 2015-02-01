# -*- coding: utf-8 -*-
import sys
import datetime
import os
import time

from django.contrib.auth.models import Permission
from django.contrib.sites.models import Site
from django.core.cache import cache
from django.core.urlresolvers import clear_url_caches
from django.test import LiveServerTestCase
from django.utils import unittest
from djangocms_link.models import Link
from djangocms_style.models import Style
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.select import Select
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoAlertPresentException
from selenium.common.exceptions import NoSuchElementException
from selenium.common.exceptions import TimeoutException

from cms.api import create_page, create_title, add_plugin
from cms.appresolver import clear_app_resolvers
from cms.apphook_pool import apphook_pool
from cms.exceptions import AppAlreadyRegistered
from cms.models import Page, CMSPlugin
from cms.test_utils.project.placeholderapp.cms_app import Example1App
from cms.test_utils.project.placeholderapp.models import Example1
from cms.test_utils.testcases import SettingsOverrideTestCase
from cms.test_utils.util.context_managers import SettingsOverride
from cms.test_utils.testcases import CMSTestCase
from cms.utils.compat.dj import get_user_model
from cms.utils.conf import get_cms_setting


class CMSLiveTests(LiveServerTestCase, CMSTestCase):
    @classmethod
    def setUpClass(cls):
        super(CMSLiveTests, cls).setUpClass()
        cache.clear()
        if os.environ.get('SELENIUM', '') == '0':
            #  skip selenium tests
            raise unittest.SkipTest("Selenium env is set to 0")
        if os.environ.get("TRAVIS_BUILD_NUMBER"):
            if not all([
                    os.environ.get('SAUCE_USERNAME', None),
                    os.environ.get('SAUCE_ACCESS_KEY', None)
            ]):
                raise unittest.SkipTest("Cannot connect to Sauce Labs")
            capabilities = dict(**webdriver.DesiredCapabilities.CHROME)
            capabilities['version'] = '31'
            capabilities['platform'] = 'OS X 10.9'
            capabilities['name'] = 'django CMS'
            capabilities['build'] = os.environ["TRAVIS_BUILD_NUMBER"]
            capabilities['tags'] = [
                os.environ.get("TRAVIS_PYTHON_VERSION"), "CI"
            ]
            username = os.environ["SAUCE_USERNAME"]
            access_key = os.environ["SAUCE_ACCESS_KEY"]
            hub_url = "http://{0}:{1}@ondemand.saucelabs.com/wd/hub".format(
                username,
                access_key
            )
            cls.driver = webdriver.Remote(
                desired_capabilities=capabilities,
                command_executor=hub_url
            )
            cls.driver.implicitly_wait(30)
        else:
            driver = os.environ.get('SELENIUM_DRIVER_CLASS', 'Firefox')
            cls.driver = getattr(webdriver, driver)()
            cls.driver.implicitly_wait(5)
        cls.accept_next_alert = True

    @classmethod
    def tearDownClass(cls):
        cls.driver.quit()
        super(CMSLiveTests, cls).tearDownClass()

    def tearDown(self):
        super(CMSLiveTests, self).tearDown()
        Page.objects.all().delete()  # somehow the sqlite transaction got lost.
        cache.clear()

    def _login(self):
        user_model = get_user_model()
        username = 'admin@example.com'
        password = 'admin'
        user_instance, _ = user_model.objects.get_or_create(
            **{user_model.USERNAME_FIELD: username}
        )
        user_instance.set_password(password)
        user_instance.is_superuser = True
        user_instance.is_active = True
        user_instance.is_staff = True
        user_instance.save()
        url = '%s/?%s' % (self.live_server_url, get_cms_setting('CMS_TOOLBAR_URL__EDIT_ON'))
        self.driver.get(url)

        self.assertRaises(
            NoSuchElementException,
            self.driver.find_element_by_class_name, 'cms_toolbar-item_logout'
        )

        username_input = self.driver.find_element_by_id("id_cms-username")
        username_input.send_keys(username)
        password_input = self.driver.find_element_by_id("id_cms-password")
        password_input.send_keys(password)
        password_input.submit()
        self.wait_page_loaded()

    def wait_until(self, callback, timeout=10):
        """
        Helper function that blocks the execution of the tests until the
        specified callback returns a value that is not falsy. This function can
        be called, for example, after clicking a link or submitting a form.
        See the other public methods that call this function for more details.
        """
        WebDriverWait(self.driver, timeout).until(callback)

    def wait_loaded_tag(self, tag_name, timeout=10):
        """
        Helper function that blocks until the element with the given tag name
        is found on the page.
        """
        self.wait_until(
            lambda driver: driver.find_element_by_tag_name(tag_name),
            timeout
        )

    def wait_page_loaded(self):
        """
        Block until page has started to load.
        """
        try:
            # Wait for the next page to be loaded
            self.wait_loaded_tag('body')
        except TimeoutException:
            # IE7 occasionnally returns an error "Internet Explorer cannot
            # display the webpage" and doesn't load the next page. We just
            # ignore it.
            pass

    def fast_check_element_exists(self, css_selector, timeout=5):
        try:
            self.wait_until(
                lambda driver: driver.find_element_by_css_selector(css_selector),
                timeout=timeout
            )
            return True
        except TimeoutException:
            return False

    def is_element_present(self, how, what):
        try:
            self.driver.find_element(by=how, value=what)
        except NoSuchElementException:
            return False
        return True

    def is_alert_present(self):
        try:
            self.driver.switch_to.alert()
        except NoAlertPresentException:
            return False
        return True

    def close_alert_and_get_its_text(self):
        try:
            alert = self.driver.switch_to.alert()
            alert_text = alert.text
            if self.accept_next_alert:
                alert.accept()
            else:
                alert.dismiss()
            return alert_text
        finally:
            self.accept_next_alert = True

    def reload_urls(self):
        """
         Code borrowed from ApphooksTestCase
        """
        from django.conf import settings

        url_modules = [
            'cms.urls',
            # TODO: Add here intermediary modules which may
            #       include() the 'cms.urls' if it isn't included
            #       directly in the root urlconf.
            # '...',
            'cms.test_utils.project.second_cms_urls_for_apphook_tests',
            'cms.test_utils.project.urls_for_apphook_tests',
            settings.ROOT_URLCONF,
        ]

        clear_app_resolvers()
        clear_url_caches()

        for module in url_modules:
            if module in sys.modules:
                del sys.modules[module]


class ToolbarBasicTests(CMSLiveTests):

    def setUp(self):
        self.user = self.get_superuser()
        Site.objects.create(domain='example.org', name='example.org')
        self.base_url = self.live_server_url
        self.driver.implicitly_wait(2)
        super(ToolbarBasicTests, self).setUp()

    def test_toolbar_login(self):
        User = get_user_model()
        create_page('Home', 'simple.html', 'en', published=True)
        url = '%s/?%s' % (self.live_server_url, get_cms_setting('CMS_TOOLBAR_URL__EDIT_ON'))
        self.assertTrue(User.objects.all().count(), 1)
        self.driver.get(url)
        self.assertRaises(NoSuchElementException, self.driver.find_element_by_class_name, 'cms_toolbar-item_logout')
        username_input = self.driver.find_element_by_id("id_cms-username")
        username_input.send_keys(getattr(self.user, User.USERNAME_FIELD))
        password_input = self.driver.find_element_by_id("id_cms-password")
        password_input.send_keys(getattr(self.user, User.USERNAME_FIELD))
        password_input.submit()
        self.wait_page_loaded()
        self.assertTrue(self.driver.find_element_by_class_name('cms_toolbar-item-navigation'))

    def test_toolbar_login_view(self):
        User = get_user_model()
        create_page('Home', 'simple.html', 'en', published=True)
        ex1 = Example1.objects.create(
            char_1='char_1', char_2='char_1', char_3='char_3', char_4='char_4',
            date_field=datetime.datetime.now()
        )
        try:
            apphook_pool.register(Example1App)
        except AppAlreadyRegistered:
            pass
        self.reload_urls()
        create_page('apphook', 'simple.html', 'en', published=True,
                    apphook=Example1App)


        url = '%s/%s/?%s' % (self.live_server_url, 'apphook/detail/%s' % ex1.pk, get_cms_setting('CMS_TOOLBAR_URL__EDIT_ON'))
        self.driver.get(url)
        self.wait_page_loaded()
        username_input = self.driver.find_element_by_id("id_cms-username")
        username_input.send_keys(getattr(self.user, User.USERNAME_FIELD))
        password_input = self.driver.find_element_by_id("id_cms-password")
        password_input.send_keys("what")
        password_input.submit()
        self.wait_page_loaded()
        self.assertTrue(self.driver.find_element_by_class_name('cms_error'))

    def test_toolbar_login_cbv(self):
        User = get_user_model()
        try:
            apphook_pool.register(Example1App)
        except AppAlreadyRegistered:
            pass
        self.reload_urls()
        create_page('Home', 'simple.html', 'en', published=True)
        ex1 = Example1.objects.create(
            char_1='char_1', char_2='char_1', char_3='char_3', char_4='char_4',
            date_field=datetime.datetime.now()
        )
        create_page('apphook', 'simple.html', 'en', published=True,
                    apphook=Example1App)
        url = '%s/%s/?%s' % (self.live_server_url, 'apphook/detail/class/%s' % ex1.pk, get_cms_setting('CMS_TOOLBAR_URL__EDIT_ON'))
        self.driver.get(url)
        username_input = self.driver.find_element_by_id("id_cms-username")
        username_input.send_keys(getattr(self.user, User.USERNAME_FIELD))
        password_input = self.driver.find_element_by_id("id_cms-password")
        password_input.send_keys("what")
        password_input.submit()
        self.wait_page_loaded()
        self.assertTrue(self.driver.find_element_by_class_name('cms_error'))

    def test_basic_add_pages(self):
        with SettingsOverride(DEBUG=True):
            User = get_user_model()
            self.assertEqual(Page.objects.all().count(), 0)
            self.assertTrue(User.objects.all().count(), 1)
            driver = self.driver
            driver.get(self.base_url + "/de/")
            driver.find_element_by_id("add-page").click()
            driver.find_element_by_id("id_username").clear()
            driver.find_element_by_id("id_username").send_keys(getattr(self.user, User.USERNAME_FIELD))
            driver.find_element_by_id("id_password").clear()
            driver.find_element_by_id("id_password").send_keys(getattr(self.user, User.USERNAME_FIELD))
            driver.find_element_by_css_selector("input[type=\"submit\"]").click()
            driver.find_element_by_name("_save").click()
            driver.find_element_by_link_text(u"Seite hinzufügen").click()
            driver.find_element_by_id("id_title").clear()
            driver.find_element_by_id("id_title").send_keys("SubPage")
            driver.find_element_by_name("_save").click()


class PlaceholderBasicTests(CMSLiveTests, SettingsOverrideTestCase):
    settings_overrides = {
        'LANGUAGE_CODE': 'en',
        'LANGUAGES': (('en', 'English'),
                      ('it', 'Italian')),
        'CMS_LANGUAGES': {
            1: [{
                'code': 'en',
                'name': 'English',
                'public': True
            }, {
                'code': 'it',
                'name': 'Italian',
                'public': True
            }],
            'default': {
                'public': True,
                'hide_untranslated': False,
            }
        },
        'SITE_ID': 1,
    }

    def setUp(self):
        Site.objects.create(domain='example.org', name='example.org')

        self.page = create_page('Home', 'simple.html', 'en', published=True)
        self.italian_title = create_title('it', 'Home italian', self.page)

        self.placeholder = self.page.placeholders.all()[0]

        add_plugin(self.placeholder, 'TextPlugin', 'en', body='test')

        self.base_url = self.live_server_url

        self.user = self._create_user('admin', True, True, True)

        self.driver.implicitly_wait(5)

        super(PlaceholderBasicTests, self).setUp()

    def test_copy_from_language(self):
        self._login()
        self.driver.get('%s/it/?%s' % (self.live_server_url, get_cms_setting('CMS_TOOLBAR_URL__EDIT_ON')))

        # check if there are no plugins in italian version of the page

        italian_plugins = self.page.placeholders.all()[0].get_plugins_list('it')
        self.assertEqual(len(italian_plugins), 0)

        build_button = self.driver.find_element_by_css_selector('.cms_toolbar-item-cms-mode-switcher a[href="?%s"]' % get_cms_setting('CMS_TOOLBAR_URL__BUILD'))
        build_button.click()

        submenu = self.driver.find_element_by_css_selector('.cms_dragbar .cms_submenu')

        hov = ActionChains(self.driver).move_to_element(submenu)
        hov.perform()

        submenu_link_selector = '.cms_submenu-item a[data-rel="copy-lang"][data-language="en"]'
        WebDriverWait(self.driver, 10).until(EC.visibility_of_element_located((By.CSS_SELECTOR, submenu_link_selector)))
        copy_from_english = self.driver.find_element_by_css_selector(submenu_link_selector)
        copy_from_english.click()

        # Done, check if the text plugin was copied and it is only one

        WebDriverWait(self.driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, '.cms_draggable:nth-child(1)')))

        italian_plugins = self.page.placeholders.all()[0].get_plugins_list('it')
        self.assertEqual(len(italian_plugins), 1)

        plugin_instance = italian_plugins[0].get_plugin_instance()[0]

        self.assertEqual(plugin_instance.body, 'test')

    def test_copy_to_from_clipboard(self):
        self.assertEqual(CMSPlugin.objects.count(), 1)
        self._login()

        build_button = self.driver.find_element_by_css_selector('.cms_toolbar-item-cms-mode-switcher a[href="?%s"]' % get_cms_setting('CMS_TOOLBAR_URL__BUILD'))
        build_button.click()

        cms_draggable = self.driver.find_element_by_css_selector('.cms_draggable:nth-child(1)')

        hov = ActionChains(self.driver).move_to_element(cms_draggable)
        hov.perform()

        submenu = cms_draggable.find_element_by_css_selector('.cms_submenu')

        hov = ActionChains(self.driver).move_to_element(submenu)
        hov.perform()

        copy = submenu.find_element_by_css_selector('a[data-rel="copy"]')
        copy.click()

        time.sleep(0.2)
        clipboard = self.driver.find_element_by_css_selector('.cms_clipboard')

        WebDriverWait(self.driver, 10).until(lambda driver: clipboard.is_displayed())

        hov = ActionChains(self.driver).move_to_element(clipboard)
        hov.perform()

        # necessary sleeps for making a "real" drag and drop, that works with the clipboard

        time.sleep(0.1)

        self.assertEqual(CMSPlugin.objects.count(), 2)

        drag = ActionChains(self.driver).click_and_hold(
            clipboard.find_element_by_css_selector('.cms_draggable:nth-child(1)')
        )

        drag.perform()

        time.sleep(0.1)

        drag = ActionChains(self.driver).move_to_element(
            self.driver.find_element_by_css_selector('.cms_dragarea-1')
        )
        drag.perform()

        time.sleep(0.2)

        drag = ActionChains(self.driver).move_by_offset(
            0, 10
        ).release()

        drag.perform()

        time.sleep(0.5)

        self.assertEqual(CMSPlugin.objects.count(), 3)

        plugins = self.page.placeholders.all()[0].get_plugins_list('en')

        self.assertEqual(len(plugins), 2)


class StaticPlaceholderPermissionTests(CMSLiveTests, SettingsOverrideTestCase):
    settings_overrides = {
        'SITE_ID': 1,
        'CMS_PERMISSION': False,
    }

    def setUp(self):
        Site.objects.create(domain='example.org', name='example.org')

        self.page = create_page('Home', 'static.html', 'en', published=True)

        self.base_url = self.live_server_url

        self.placeholder_name = 'cms_placeholder-5'

        self.username = 'testuser'

        self.user = self._create_user(self.username, is_staff=True)
        self.user.user_permissions = Permission.objects.exclude(
            codename="edit_static_placeholder"
        )

        self.driver.implicitly_wait(2)

        super(StaticPlaceholderPermissionTests, self).setUp()

    def test_static_placeholders_permissions(self):
        # login
        url = '%s/?%s' % (
            self.live_server_url,
            get_cms_setting('CMS_TOOLBAR_URL__EDIT_ON')
        )
        self.driver.get(url)

        self.assertRaises(
            NoSuchElementException,
            self.driver.find_element_by_class_name, 'cms_toolbar-item_logout'
        )
        username_input = self.driver.find_element_by_id("id_cms-username")
        username_input.send_keys(self.username)
        password_input = self.driver.find_element_by_id("id_cms-password")
        password_input.send_keys(self.username)
        password_input.submit()
        self.wait_page_loaded()

        self.assertTrue(self.driver.find_element_by_class_name(
            'cms_toolbar-item-navigation'
        ))

        # test static placeholder permission (content of static placeholders
        # is NOT editable)
        self.driver.get('{0}/en/?{1}'.format(
            self.live_server_url,
            get_cms_setting('CMS_TOOLBAR_URL__EDIT_ON')
        ))
        self.assertRaises(
            NoSuchElementException,
            self.driver.find_element_by_class_name, self.placeholder_name
        )

        # update userpermission
        edit_permission = Permission.objects.get(
            codename="edit_static_placeholder"
        )
        self.user.user_permissions.add(edit_permission)

        # test static placeholder permission (content of static placeholders
        # is editable)
        self.driver.get('{0}/en/?{1}'.format(
            self.live_server_url,
            get_cms_setting('CMS_TOOLBAR_URL__EDIT_ON')
        ))
        self.assertTrue(
            self.driver.find_element_by_class_name(self.placeholder_name)
        )


class AddPluginTest(CMSLiveTests):
    def test_add_style_plugin(self):
        page = create_page('Home', 'simple.html', 'en', published=True)

        placeholder_id = page.placeholders.all()[0].pk

        raise unittest.SkipTest("Selenium env is set to 0")

        self._login()

        # click structure mode
        self.driver.find_element_by_css_selector('a[href="?build"]').click()
        self.wait_page_loaded()

        # open the "add plugin menu"
        placeholder_bar = self.driver.find_element_by_css_selector(
            'div.cms_dragbar-{0} div'.format(placeholder_id)
        )
        placeholder_bar.click()
        # click the Style Plugin
        placeholder_bar.find_element_by_css_selector(
            'a[href="StylePlugin"]'
        ).click()
        self.wait_page_loaded()

        # switch to the edit window iframe
        iframe = self.driver.find_element_by_css_selector(
            'div.cms_modal-frame iframe'
        )
        self.driver.switch_to.frame(iframe)

        # change the class name to "new"
        class_input = self.driver.find_element_by_id("id_class_name")
        Select(class_input).select_by_value("new")

        # submit the form
        class_input.submit()

        # wait for everything to be done
        self.wait_page_loaded()

        self.assertEqual(CMSPlugin.objects.count(), 1)
        self.assertEqual(Style.objects.count(), 1)
        link = Style.objects.get()
        self.assertEqual(link.class_name, "new")

    def test_add_plugin_in_text_plugin(self):
        page = create_page('Home', 'simple.html', 'en', published=True)

        placeholder = page.placeholders.all()[0]

        text_plugin = add_plugin(
            placeholder=placeholder,
            language='en',
            plugin_type='TextPlugin',
            body='Test'
        )

        page.publish('en')

        self._login()

        # Double click plugin
        plugin_element = self.driver.find_element_by_css_selector(
            'div.cms_plugin-{0}'.format(text_plugin.pk)
        )

        chain = ActionChains(self.driver)
        chain.double_click(plugin_element)
        chain.perform()

        # Wait for iframe to pop up
        self.wait_page_loaded()

        # Switch to iframe
        iframe = self.driver.find_element_by_css_selector(
            'div.cms_modal-frame iframe'
        )
        self.driver.switch_to.frame(iframe)

        # Find and click the CMSPlugin CKEditor plugin button
        cmsplugin = self.driver.find_element_by_css_selector(
            'span.cke_button__cmsplugins_label'
        )
        cmsplugin.click()

        # The dropdown to select the CMSPlugin is inside yet another iframe,
        # currently just hope it's the second iframe.
        # TODO: Find a more reliable way to find the correct iframe
        _, dropdown = self.driver.find_elements_by_tag_name('iframe')
        self.driver.switch_to.frame(dropdown)

        # Find the link plugin
        link = self.driver.find_element_by_css_selector('a[rel="LinkPlugin"]')
        link.click()

        raise unittest.SkipTest("Selenium env is set to 0")

        self.wait_page_loaded()

        self.driver.switch_to.parent_frame()

        dialog = self.driver.find_element_by_css_selector(
            'iframe.cke_dialog_ui_html'
        )

        # Switch to the Add Link dialog
        self.driver.switch_to.frame(dialog)

        name = self.driver.find_element_by_id('id_name')
        name.send_keys('Example')

        url = self.driver.find_element_by_id('id_url')
        url.send_keys('http://www.example.org/')
        url.submit()

        self.wait_page_loaded()

        self.assertEqual(Link.objects.count(), 1)
        link_plugin = Link.objects.get()
        self.assertEqual(link_plugin.name, 'Example')
        self.assertEqual(link_plugin.url, 'http://www.example.org/')
        self.assertEqual(link_plugin.parent_id, text_plugin.pk)

        # back to plugin view
        self.driver.switch_to.parent_frame()
        # back to actual page
        self.driver.switch_to.parent_frame()
        save_button = self.driver.find_element_by_css_selector(
            'div.cms_btn-action.default'
        )
        save_button.click()

        self.wait_page_loaded()

        self.assertEqual(Link.objects.count(), 1)
        link_plugin = Link.objects.get()
        self.assertEqual(link_plugin.name, 'Example')
        self.assertEqual(link_plugin.url, 'http://www.example.org/')
        self.assertEqual(link_plugin.parent_id, text_plugin.pk)

        paragraph = self.driver.find_element_by_css_selector(
            'div.cms_plugin-{0} p'.format(text_plugin.pk)
        )

        self.assertIn('Example', paragraph.text)
