import os

from django.conf import settings
from django.conf.urls import include
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.urls import re_path
from django.views.generic import RedirectView

from perma.views.user_management import AddUserToOrganization, AddUserToRegistrar, AddSponsoredUserToRegistrar, AddUserToAdmin, AddRegularUser
from .views.common import DirectTemplateView
from .views import user_management, common, service, link_management, error_management
from .forms import SetPasswordForm

# between 9/5/2013 and 11/13/2014,
# we created GUIDS as short as 6 chars (#-####)
# and as long as 11 chars (#-####-#### or ###########)
guid_pattern = r'(?P<guid>[a-zA-Z0-9\-]{6,11})'

urlpatterns = [
    # Common Pages
    re_path(r'^$', common.landing, name='landing'),
    re_path(r'^about/?$', common.about, name='about'),
    re_path(r'^stats/?$', DirectTemplateView.as_view(template_name='stats.html'), name='stats'),
    re_path(r'^copyright-policy/?$', DirectTemplateView.as_view(template_name='copyright_policy.html'), name='copyright_policy'),
    re_path(r'^terms-of-service/?$', DirectTemplateView.as_view(template_name='terms_of_service.html'), name='terms_of_service'),
    re_path(r'^privacy-policy/?$', DirectTemplateView.as_view(template_name='privacy_policy.html'), name='privacy_policy'),
    re_path(r'^return-policy/?$', DirectTemplateView.as_view(template_name='return_policy.html'), name='return_policy'),
    re_path(r'^contingency-plan/?$', DirectTemplateView.as_view(template_name='contingency_plan.html'), name='contingency_plan'),
    re_path(r'^contact/?$', common.contact, name='contact'),
    re_path(r'^contact/thanks/?$', common.contact_thanks, name='contact_thanks'),
    #   re_path(r'^is500/?$', DirectTemplateView.as_view(template_name='500.html'), name='is500'),
    #   re_path(r'^is404/?$', DirectTemplateView.as_view(template_name='404.html'), name='is404'),

    #Docs
    re_path(r'^docs/?$', DirectTemplateView.as_view(template_name='docs/index.html'), name='docs'),
    re_path(r'^docs/perma-link-creation/?$', DirectTemplateView.as_view(template_name='docs/perma-link-creation.html'), name='docs_perma_link_creation'),
    re_path(r'^docs/libraries/?$', DirectTemplateView.as_view(template_name='docs/libraries.html'), name='docs_libraries'),
    re_path(r'^docs/faq/?$', common.faq, name='docs_faq'),
    re_path(r'^docs/accounts/?$', DirectTemplateView.as_view(template_name='docs/accounts.html'), name='docs_accounts'),

    #Developer docs
    re_path(r'^docs/developer/?$', DirectTemplateView.as_view(template_name='docs/developer/index.html'), name='dev_docs'),

    #Services
    re_path(r'^service/stats/sums/?$', service.stats_sums, name='service_stats_sums'),
    re_path(r'^service/stats/now/?$', service.stats_now, name='service_stats_now'),
    re_path(r'^service/bookmarklet-create/?$', service.bookmarklet_create, name='service_bookmarklet_create'),
    re_path(r'^service/get-coordinates/?$', service.coordinates_from_address, name='service_coordinates_from_address'),
    #re_path(r'^service/thumbnail/%s/thumbnail.png$' % guid_pattern, service.get_thumbnail, name='service_get_thumbnail'),

    # Session/account management
    re_path(r'^login/?$', user_management.limited_login, {'template_name': 'registration/login.html'}, name='user_management_limited_login'),
    re_path(r'^login/not-active/?$', user_management.not_active, name='user_management_not_active'),
    re_path(r'^login/account-is-deactivated/?$', user_management.account_is_deactivated, name='user_management_account_is_deactivated'),
    re_path(r'^logout/?$', user_management.logout, name='logout'),
    re_path(r'^register/?$', RedirectView.as_view(url='/sign-up/', permanent=True)),

    # Users with old-style activation links should get redirected so they can generate a new one
    re_path(r'^register/password/(?P<token>.*)/?$', user_management.redirect_to_reset, name='redirect_to_reset'),

    re_path(r'^sign-up/?$', user_management.sign_up, name='sign_up'),
    re_path(r'^sign-up/courts/?$', user_management.sign_up_courts, name='sign_up_courts'),
    re_path(r'^sign-up/faculty/?$', user_management.sign_up_faculty, name='sign_up_faculty'),
    re_path(r'^sign-up/journals/?$', user_management.sign_up_journals, name='sign_up_journals'),
    re_path(r'^sign-up/firms/?$', user_management.sign_up_firm, name='sign_up_firm'),
    re_path(r'^libraries/?$', user_management.libraries, name='libraries'),

    re_path(r'^register/email/?$', user_management.register_email_instructions, name='register_email_instructions'),
    re_path(r'^register/library/?$', user_management.register_library_instructions, name='register_library_instructions'),
    re_path(r'^register/court/?$', user_management.court_request_response, name='court_request_response'),
    re_path(r'^register/firm/?$', user_management.firm_request_response, name='firm_request_response'),
    re_path(r'^password/change/?$', auth_views.PasswordChangeView.as_view(template_name='registration/password_change_form.html'), name='password_change'),
    re_path(r'^password/change/done/?$', auth_views.PasswordChangeDoneView.as_view(template_name='registration/password_change_done.html'), name='password_change_done'),
    re_path(r'^password/reset/?$', user_management.reset_password, name='password_reset'),
    re_path(
        r'^password/reset/(?P<uidb64>.+)/(?P<token>.+)/?$',
        auth_views.PasswordResetConfirmView.as_view(
            form_class=SetPasswordForm,
            template_name='registration/password_reset_confirm.html'
        ),
        name='password_reset_confirm'
    ),
    re_path(r'^password/reset/complete/?$', auth_views.PasswordResetCompleteView.as_view(template_name='registration/password_reset_complete.html'), name='password_reset_complete'),
    re_path(r'^password/reset/done/?$', auth_views.PasswordResetDoneView.as_view(template_name='registration/password_reset_done.html'), name='password_reset_done'),
    re_path(r'^api_key/create/?$', user_management.api_key_create, name='api_key_create'),

    # Settings
    re_path(r'^settings/profile/?$', user_management.settings_profile, name='user_management_settings_profile'),
    re_path(r'^settings/profile/delete/?$', user_management.delete_account, name='user_management_delete_account'),
    re_path(r'^settings/password/?$', user_management.settings_password, name='user_management_settings_password'),
    re_path(r'^settings/affiliations/?$', user_management.settings_affiliations, name='user_management_settings_affiliations'),
    re_path(r'^settings/organizations-change-privacy/(?P<org_id>\d+)/?$', user_management.settings_organizations_change_privacy, name='user_management_settings_organizations_change_privacy'),
    re_path(r'^settings/tools/?$', user_management.settings_tools, name='user_management_settings_tools'),
    re_path(r'^settings/usage-plan/?$', user_management.settings_usage_plan, name='user_management_settings_usage_plan'),
    re_path(r'^settings/subscription/?$', RedirectView.as_view(url='/settings/usage-plan/', permanent=True), name='user_management_settings_subscription'),
    re_path(r'^settings/subscription/cancel/?$', user_management.settings_subscription_cancel, name='user_management_settings_subscription_cancel'),
    re_path(r'^settings/subscription/update/?$', user_management.settings_subscription_update, name='user_management_settings_subscription_update'),

    # Link management
    re_path(r'^manage/?$', RedirectView.as_view(url='/manage/create/', permanent=False)),
    re_path(r'^manage/create/?$', link_management.create_link, name='create_link'),
    re_path(r'^manage/create/(?P<org_id>\d+)/?$', RedirectView.as_view(url='/manage/create/', permanent=False), name='create_link_with_org'),
    re_path(fr'^manage/delete-link/{guid_pattern}/?$', link_management.user_delete_link, name='user_delete_link'),
    # we used to serve an important page here. No longer. Redirect in case anyone has this bookmarked.
    re_path(r'^manage/links(?P<path>/.*)?$', RedirectView.as_view(url='/manage/create/', permanent=False), name='link_browser'),

    # user management
    re_path(r'^manage/stats/?(?P<stat_type>.*?)?/?$', user_management.stats, name='user_management_stats'),

    re_path(r'^manage/registrars/?$', user_management.manage_registrar, name='user_management_manage_registrar'),
    re_path(r'^manage/registrars/(?P<registrar_id>\d+)/?$', user_management.manage_single_registrar, name='user_management_manage_single_registrar'),
    re_path(r'^manage/registrars/approve/(?P<registrar_id>\d+)/?$', user_management.approve_pending_registrar, name='user_management_approve_pending_registrar'),

    re_path(r'^manage/organizations/?$', user_management.manage_organization, name='user_management_manage_organization'),
    re_path(r'^manage/organizations/(?P<org_id>\d+)/?$', user_management.manage_single_organization, name='user_management_manage_single_organization'),
    re_path(r'^manage/organization/(?P<org_id>\d+)/delete/?$', user_management.manage_single_organization_delete, name='user_management_manage_single_organization_delete'),

    re_path(r'^manage/admin-users/?$', user_management.manage_admin_user, name='user_management_manage_admin_user'),
    re_path(r'^manage/admin-users/add-user/?$', AddUserToAdmin.as_view(), name='user_management_admin_user_add_user'),
    re_path(r'^manage/admin-user/(?P<user_id>\d+)/delete/?$', user_management.manage_single_admin_user_delete, name='user_management_manage_single_admin_user_delete'),
    re_path(r'^manage/admin-users/(?P<user_id>\d+)/remove/?$', user_management.manage_single_admin_user_remove, name='user_management_manage_single_admin_user_remove'),

    re_path(r'^manage/registrar-users/?$', user_management.manage_registrar_user, name='user_management_manage_registrar_user'),
    re_path(r'^manage/registrar-users/add-user/?$', AddUserToRegistrar.as_view(), name='user_management_registrar_user_add_user'),
    re_path(r'^manage/registrar-users/(?P<user_id>\d+)/?$', user_management.manage_single_registrar_user, name='user_management_manage_single_registrar_user'),
    re_path(r'^manage/registrar-user/(?P<user_id>\d+)/delete/?$', user_management.manage_single_registrar_user_delete, name='user_management_manage_single_registrar_user_delete'),
    re_path(r'^manage/registrar-users/(?P<user_id>\d+)/reactivate/?$', user_management.manage_single_registrar_user_reactivate, name='user_management_manage_single_registrar_user_reactivate'),
    re_path(r'^manage/registrar-users/(?P<user_id>\d+)/remove/?$', user_management.manage_single_registrar_user_remove, name='user_management_manage_single_registrar_user_remove'),

    re_path(r'^manage/users/?$', user_management.manage_user, name='user_management_manage_user'),
    re_path(r'^manage/users/add-user/?$', AddRegularUser.as_view(), name='user_management_user_add_user'),
    re_path(r'^manage/users/(?P<user_id>\d+)/?$', user_management.manage_single_user, name='user_management_manage_single_user'),
    re_path(r'^manage/users/(?P<user_id>\d+)/delete/?$', user_management.manage_single_user_delete, name='user_management_manage_single_user_delete'),
    re_path(r'^manage/users/(?P<user_id>\d+)/reactivate/?$', user_management.manage_single_user_reactivate, name='user_management_manage_single_user_reactivate'),
    re_path(r'^manage/users/resend-activation/(?P<user_id>\d+)/?$', user_management.resend_activation, name='user_management_resend_activation'),

    re_path(r'^manage/organization-users/?$', user_management.manage_organization_user, name='user_management_manage_organization_user'),
    re_path(r'^manage/organization-users/add-user/?$', AddUserToOrganization.as_view(), name='user_management_organization_user_add_user'),
    re_path(r'^manage/organization-users/(?P<user_id>\d+)/?$', user_management.manage_single_organization_user, name='user_management_manage_single_organization_user'),
    re_path(r'^manage/organization-users/(?P<user_id>\d+)/delete/?$', user_management.manage_single_organization_user_delete, name='user_management_manage_single_organization_user_delete'),
    re_path(r'^manage/organization-users/(?P<user_id>\d+)/reactivate/?$', user_management.manage_single_organization_user_reactivate, name='user_management_manage_single_organization_user_reactivate'),
    re_path(r'^manage/organization-users/(?P<user_id>\d+)/remove/?$', user_management.manage_single_organization_user_remove, name='user_management_manage_single_organization_user_remove'),

    re_path(r'^manage/sponsored-users/?$', user_management.manage_sponsored_user, name='user_management_manage_sponsored_user'),
    re_path(r'^manage/sponsored-users/add-user/?$', AddSponsoredUserToRegistrar.as_view(), name='user_management_sponsored_user_add_user'),
    re_path(r'^manage/sponsored-users/(?P<user_id>\d+)/?$', user_management.manage_single_sponsored_user, name='user_management_manage_single_sponsored_user'),
    re_path(r'^manage/sponsored-users/(?P<user_id>\d+)/delete/?$', user_management.manage_single_sponsored_user_delete, name='user_management_manage_single_sponsored_user_delete'),
    re_path(r'^manage/sponsored-users/(?P<user_id>\d+)/reactivate/?$', user_management.manage_single_sponsored_user_reactivate, name='user_management_manage_single_sponsored_user_reactivate'),
    re_path(r'^manage/sponsored-users/(?P<user_id>\d+)/remove/(?P<registrar_id>\d+)/?$', user_management.manage_single_sponsored_user_remove, name='user_management_manage_single_sponsored_user_remove'),
    re_path(r'^manage/sponsored-users/(?P<user_id>\d+)/readd/(?P<registrar_id>\d+)/?$', user_management.manage_single_sponsored_user_readd, name='user_management_manage_single_sponsored_user_readd'),
    re_path(r'^manage/sponsored-users/(?P<user_id>\d+)/links/(?P<registrar_id>\d+)/?$', user_management.manage_single_sponsored_user_links, name='user_management_manage_single_sponsored_user_links'),

    re_path(r'^manage/account/leave-organization/(?P<org_id>\d+)/?$', user_management.organization_user_leave_organization, name='user_management_organization_user_leave_organization'),
    #    re_path(r'^manage/users/?$', 'manage.users', name='manage_users'),
    #    re_path(r'^manage/activity/?$', 'manage.activity', name='manage_activity'),

    # error management
    re_path(r'^manage/errors/resolve/?$', error_management.resolve, name='error_management_resolve'),
    re_path(r'^manage/errors/?$', error_management.get_all, name='error_management_get_all'),
    re_path(r'^errors/new/?$', error_management.post_new, name='error_management_post_new'),

    # memento support
    re_path(r'timemap/(?P<response_format>link|json|html)/(?P<url>.+)$', common.timemap, name='timemap'),
    re_path(r'timegate/(?P<url>.+)$', common.timegate, name='timegate'),

    # serve warcs with .warc file extension for use in client-side playback
    re_path(r'^(?P<guid>[^\./]+)\.warc$', common.serve_warc, name='serve_warc'),

    # Our Perma ID catchall
    re_path(r'^(?P<guid>[^\./]+)/?$', common.single_permalink, name='single_permalink'),

    # robots.txt
    re_path(r'^robots\.txt$', common.robots_txt, name='robots.txt'),
]


if settings.DEBUG:
    # debug-only serving of media assets
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    # django toolbar
    if os.environ.get('DEBUG_TOOLBAR'):
        import debug_toolbar  # noqa
        urlpatterns = [
            re_path(r'^__debug__/', include(debug_toolbar.urls)),
        ] + urlpatterns

# views that only load when running our tests:
if settings.TESTING:
    from .tests import views as test_views
    urlpatterns += [
        re_path(r'tests/client_ip$', test_views.client_ip)
    ]
