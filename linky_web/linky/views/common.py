from django.shortcuts import render_to_response, get_object_or_404
from django.http import HttpResponseRedirect
from django.core.context_processors import csrf
from django.core.urlresolvers import reverse
from django.conf import settings

from datetime import datetime
from urlparse import urlparse
import urllib2
import logging

from linky.models import Link, Asset
from linky.utils import base
from ratelimit.decorators import ratelimit

logger = logging.getLogger(__name__)


@ratelimit(method='GET', rate=settings.MINUTE_LIMIT, block='True')
@ratelimit(method='GET', rate=settings.HOUR_LIMIT, block='True')
@ratelimit(method='GET', rate=settings.DAY_LIMIT, block='True')
def landing(request):
    """
    The landing page
    """

    if request.user.id >= 0:
      linky_links = list(Link.objects.filter(created_by=request.user).order_by('-creation_timestamp'))
    else:
      linky_links = None;

    context = {'this_page': 'landing', 'host': request.get_host(), 'user': request.user, 'linky_links': linky_links, 'next': request.get_full_path()}
    context.update(csrf(request))

    return render_to_response('landing.html', context)


def about(request):
    """
    The about page
    """

    context = {'host': request.get_host()}

    return render_to_response('about.html', context)


def faq(request):
    """
    The FAQ page
    """
    
    return render_to_response('faq.html', {})


def contact(request):
    """
    The contact page
    """
    
    return render_to_response('contact.html', {})


def terms_of_service(request):
    """
    The terms of service page
    """
    
    return render_to_response('terms_of_service.html', {})


def privacy_policy(request):
    """
    The privacy policy page
    """
    
    return render_to_response('privacy_policy.html', {})


def tools(request):
    """
    The tools page
    """
    
    return render_to_response('tools.html', {})


@ratelimit(method='GET', rate=settings.MINUTE_LIMIT, block='True')
@ratelimit(method='GET', rate=settings.HOUR_LIMIT, block='True')
@ratelimit(method='GET', rate=settings.DAY_LIMIT, block='True')
def single_linky(request, linky_guid):
    """
    Given a Linky ID, serve it up. Vetting also takes place here.
    """

    if request.method == 'POST' and request.user.is_authenticated():
        Link.objects.filter(guid=linky_guid).update(vested = True, vested_by_editor = request.user, vested_timestamp = datetime.now())

        return HttpResponseRedirect('/%s/' % linky_guid)
    else:

        link = get_object_or_404(Link, guid=linky_guid)

        link.view_count += 1
        link.save()

        asset = Asset.objects.get(link=link)


        # User requested archive type
        serve_type = 'live'

        if 'type' in request.REQUEST:
            requested_type = request.REQUEST['type']
        
            if requested_type == 'image' and asset.image_capture and asset.image_capture != 'pending':
                serve_type = 'image'
            elif requested_type == 'pdf' and asset.pdf_capture and asset.pdf_capture != 'pending':
                serve_type = 'pdf'
            elif requested_type == 'text' and asset.instapaper_cap and asset.instapaper_cap != 'pending':
                serve_type = 'text'
            elif requested_type == 'source' and asset.warc_capture and asset.warc_capture != 'pending':
                serve_type = 'source'
            
            
        # If we are going to serve up the live version of the site, let's make sure it's iframe-able
        if serve_type == 'live':
            try:
                response = urllib2.urlopen(link.submitted_url)
                if 'X-Frame-Options' in response.headers:
                    # TODO actually check if X-Frame-Options specifically allows requests from us
                    display_iframe = False
                else:
                    display_iframe = True
            except urllib2.URLError:
                # Something is broken with the site, so we might as well display it in an iFrame so the user knows
                display_iframe = True

        asset= Asset.objects.get(link__guid=link.guid)

        created_datestamp = link.creation_timestamp
        pretty_date = created_datestamp.strftime("%B %d, %Y %I:%M GMT")

        context = {'linky': link, 'asset': asset, 'pretty_date': pretty_date, 'user': request.user, 'next': request.get_full_path(),
                   'display_iframe': display_iframe, 'serve_type': serve_type}

        context.update(csrf(request))

    return render_to_response('single-linky.html', context)


def rate_limit(request, exception):
    """
    When a user hits a rate limit, send them here.
    """
    
    return render_to_response("rate_limit.html")
