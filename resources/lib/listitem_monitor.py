#!/usr/bin/python
# -*- coding: utf-8 -*-

'''
    script.skin.helper.service
    listitem_monitor.py
    monitor the kodi listitems and providing additional information
'''

import os, sys
import threading
if sys.version_info.major == 3:
    import _thread as thread
else:
    import thread
from resources.lib.utils import log_msg, log_exception, get_current_content_type, kodi_json, prepare_win_props, merge_dict, getCondVisibility, try_decode
import xbmc
from simplecache import SimpleCache


class ListItemMonitor(threading.Thread):
    '''Our main class monitoring the kodi listitems and providing additional information'''
    event = None
    exit = False
    delayed_task_interval = 1795
    listitem_details = {}
    all_window_props = {}
    cur_listitem = ""
    last_folder = ""
    last_listitem = ""
    foldercontent = {}
    screensaver_setting = None
    screensaver_disabled = False
    lookup_busy = {}
    enable_extendedart = False
    enable_musicart = False
    enable_animatedart = False
    enable_extrafanart = False
    enable_extraposter = False
    enable_pvrart = False
    enable_forcedviews = False

    def __init__(self, *args, **kwargs):
        self.cache = SimpleCache()
        self.metadatautils = kwargs.get("metadatautils")
        self.win = kwargs.get("win")
        self.kodimonitor = kwargs.get("monitor")
        self.event = threading.Event()
        threading.Thread.__init__(self, *args)

    def stop(self):
        '''called when the thread has to stop working'''
        log_msg("ListItemMonitor - stop called")
        self.exit = True
        self.cache.close()
        self.event.set()
        self.event.clear()
        self.join(1)

    def run(self):
        '''our main loop monitoring the listitem and folderpath changes'''
        log_msg("ListItemMonitor - started")
        self.get_settings()

        while not self.exit:

            # check screensaver and OSD
            self.check_screensaver()
            self.check_osd()

            # do some background stuff every 30 minutes
            if (self.delayed_task_interval >= 1800) and not self.exit:
                thread.start_new_thread(self.do_background_work, ())
                self.delayed_task_interval = 0

            # skip if any of the artwork context menus is opened
            if self.win.getProperty("SkinHelper.Artwork.ManualLookup"):
                self.reset_win_props()
                self.last_listitem = ""
                self.listitem_details = {}
                self.kodimonitor.waitForAbort(3)
                self.delayed_task_interval += 3

            # skip when modal dialogs are opened (e.g. textviewer in musicinfo dialog)
            elif getCondVisibility(
                    "Window.IsActive(DialogSelect.xml) | Window.IsActive(progressdialog) | "
                    "Window.IsActive(contextmenu) | Window.IsActive(busydialog)"):
                self.kodimonitor.waitForAbort(2)
                self.delayed_task_interval += 2
                self.last_listitem = ""

            # skip when container scrolling
            elif getCondVisibility(
                    "Container.OnScrollNext | Container.OnScrollPrevious | Container.Scrolling"):
                self.kodimonitor.waitForAbort(1)
                self.delayed_task_interval += 1
                self.last_listitem = ""

            # media window is opened or widgetcontainer set - start listitem monitoring!
            elif getCondVisibility("[Window.IsMedia | !String.IsEmpty(Window(Home).Property(SkinHelper.WidgetContainer))] + "
                                                                            "!ListItem.IsParentFolder"):
                self.monitor_listitem()
                self.kodimonitor.waitForAbort(0.15)
                self.delayed_task_interval += 0.15

            # flush any remaining window properties
            elif self.all_window_props:
                self.reset_win_props()
                self.win.clearProperty("SkinHelper.ContentHeader")
                self.win.clearProperty("contenttype")
                self.win.clearProperty("curlistitem")
                self.last_listitem = ""

            # other window active - do nothing
            else:
                self.kodimonitor.waitForAbort(1)
                self.delayed_task_interval += 1

    def get_settings(self):
        '''collect our skin settings that control the monitoring'''
        self.enable_extendedart = getCondVisibility("Skin.HasSetting(SkinHelper.EnableExtendedArt)") == 1
        self.enable_musicart = getCondVisibility("Skin.HasSetting(SkinHelper.EnableMusicArt)") == 1
        self.enable_animatedart = getCondVisibility("Skin.HasSetting(SkinHelper.EnableAnimatedPosters)") == 1
        self.enable_extrafanart = getCondVisibility("Skin.HasSetting(SkinHelper.EnableExtraFanart)") == 1
        self.enable_extraposter = getCondVisibility("Skin.HasSetting(SkinHelper.EnableExtraPoster)") == 1
        self.enable_pvrart = getCondVisibility(
            "Skin.HasSetting(SkinHelper.EnablePVRThumbs) + PVR.HasTVChannels + !String.Contains(Container.FolderPath,pvr://channels/radio/)") == 1
        self.enable_forcedviews = getCondVisibility("Skin.HasSetting(SkinHelper.ForcedViews.Enabled)") == 1
        studiologos_path = xbmc.getInfoLabel("Skin.String(SkinHelper.StudioLogos.Path)")
        if studiologos_path != self.metadatautils.studiologos_path:
            self.listitem_details = {}
            self.metadatautils.studiologos_path = studiologos_path
        # set additional window props to control contextmenus as using the skinsetting gives unreliable results
        for skinsetting in ["EnableAnimatedPosters", "EnableMusicArt", "EnablePVRThumbs"]:
            if getCondVisibility("Skin.HasSetting(SkinHelper.%s)" % skinsetting):
                self.win.setProperty("SkinHelper.%s" % skinsetting, "enabled")
            else:
                self.win.clearProperty("SkinHelper.%s" % skinsetting)

    def monitor_listitem(self):
        '''Monitor listitem details'''

        cur_folder, cont_prefix = self.get_folderandprefix()
        # identify current listitem - prefer parent folder (tvshows, music)
        cur_listitem = try_decode(xbmc.getInfoLabel(
            "$INFO[%sListItem.TvshowTitle]$INFO[%sListItem.Artist]$INFO[%sListItem.Album]" %
            (cont_prefix, cont_prefix, cont_prefix)))
        if not cur_listitem:
            # fallback to generic approach
            cur_listitem = try_decode(xbmc.getInfoLabel(
                "$INFO[%sListItem.Label]$INFO[%sListItem.DBID]$INFO[%sListItem.Title]" %
                (cont_prefix, cont_prefix, cont_prefix)))
                        
        if self.exit:
            return

        # perform actions if the container path has changed
        if cur_folder != self.last_folder and not (cur_listitem and cur_listitem == self.last_listitem):
            self.reset_win_props()
            self.get_settings()
            self.last_folder = cur_folder
            self.last_listitem = None
            content_type = self.get_content_type(cur_folder, cur_listitem, cont_prefix)
            # additional actions to perform when we have a valid contenttype and no widget container
            if not cont_prefix and content_type:
                self.set_forcedview(content_type)
                self.set_content_header(content_type)
        else:
            content_type = self.get_content_type(cur_folder, cur_listitem, cont_prefix)

        if self.exit:
            return

        # only perform actions when the listitem has actually changed
        if cur_listitem != self.last_listitem:
            self.last_listitem = cur_listitem
            self.win.setProperty("cur_listitem", cur_listitem)
            if cur_listitem and cur_listitem != "..":
                # set listitem details in background thread
                thread.start_new_thread(
                    self.set_listitem_details, (cur_listitem, content_type, cont_prefix))

    def get_folderandprefix(self):
        '''get the current folder and prefix'''
        cur_folder = ""
        cont_prefix = ""
        try:
            widget_container = try_decode(self.win.getProperty("SkinHelper.WidgetContainer"))
            if getCondVisibility("Window.IsActive(movieinformation)|Window.IsActive(DialogPVRInfo.xml)|Window.IsActive(DialogMusicInfo.xml)|Window.IsActive(script-script.extendedinfo-DialogVideoInfo.xml) | Window.IsActive(DialogPVRChannelGuide.xml) | Window.IsActive(DialogPVRChannelsOSD.xml)"):
                cont_prefix = ""
                cur_folder = try_decode(xbmc.getInfoLabel(
                    "$INFO[Window.Property(xmlfile)]$INFO[Container.FolderPath]"
                    "$INFO[Container.NumItems]$INFO[Container.Content]"))
            elif widget_container:
                cont_prefix = "Container(%s)." % widget_container
                cur_folder = try_decode(xbmc.getInfoLabel(
                        "widget-%s-$INFO[Container(%s).NumItems]-$INFO[Container(%s).ListItemAbsolute(1).Label]" %
                        (widget_container, widget_container, widget_container)))
            else:
                cont_prefix = ""
                cur_folder = try_decode(xbmc.getInfoLabel(
                    "$INFO[Window.Property(xmlfile)]$INFO[Container.FolderPath]$INFO[Container.NumItems]$INFO[Container.Content]"))
        except Exception as exc:
            log_exception(__name__, exc)
            cur_folder = ""
            cont_prefix = ""
        return (cur_folder, cont_prefix)

    def get_content_type(self, cur_folder, cur_listitem, cont_prefix):
        '''get contenttype for current folder'''
        content_type = ""
        if cur_folder in self.foldercontent:
            content_type = self.foldercontent[cur_folder]
        elif cur_folder and cur_listitem:
            # always wait for the content_type because some listings can be slow
            for i in range(20):
                content_type = get_current_content_type(cont_prefix)
                if self.exit:
                    return ""
                if content_type:
                    break
                else:
                    xbmc.sleep(250)
            self.foldercontent[cur_folder] = content_type
        self.win.setProperty("contenttype", content_type)
        return content_type

    def check_screensaver(self):
        '''Allow user to disable screensaver on fullscreen music playback'''
        if getCondVisibility(
                "Window.IsActive(visualisation) + Skin.HasSetting(SkinHelper.DisableScreenSaverOnFullScreenMusic)"):
            if not self.screensaver_disabled:
                # disable screensaver when fullscreen music active
                self.screensaver_disabled = True
                screensaver_setting = kodi_json('Settings.GetSettingValue', '{"setting":"screensaver.mode"}')
                if screensaver_setting:
                    self.screensaver_setting = screensaver_setting
                    kodi_json('Settings.SetSettingValue', {"setting": "screensaver.mode", "value": None})
                    log_msg(
                        "Disabled screensaver while fullscreen music playback - previous setting: %s" %
                        self.screensaver_setting, xbmc.LOGINFO)
        elif self.screensaver_disabled and self.screensaver_setting:
            # enable screensaver again after fullscreen music playback was ended
            kodi_json('Settings.SetSettingValue', {"setting": "screensaver.mode", "value": self.screensaver_setting})
            self.screensaver_disabled = False
            self.screensaver_setting = None
            log_msg(
                "fullscreen music playback ended - restoring screensaver: %s" %
                self.screensaver_setting, xbmc.LOGINFO)

    @staticmethod
    def check_osd():
        '''Allow user to set a default close timeout for the OSD panels'''
        if getCondVisibility("[Window.IsActive(videoosd) + Skin.String(SkinHelper.AutoCloseVideoOSD)] | "
                                  "[Window.IsActive(musicosd) + Skin.String(SkinHelper.AutoCloseMusicOSD)]"):
            if getCondVisibility("Window.IsActive(videoosd)"):
                seconds = xbmc.getInfoLabel("Skin.String(SkinHelper.AutoCloseVideoOSD)")
                window = "videoosd"
            elif getCondVisibility("Window.IsActive(musicosd)"):
                seconds = xbmc.getInfoLabel("Skin.String(SkinHelper.AutoCloseMusicOSD)")
                window = "musicosd"
            else:
                seconds = ""
            if seconds and seconds != "0":
                while getCondVisibility("Window.IsActive(%s)" % window):
                    if getCondVisibility("System.IdleTime(%s)" % seconds):
                        if getCondVisibility("Window.IsActive(%s)" % window):
                            xbmc.executebuiltin("Dialog.Close(%s)" % window)
                    else:
                        xbmc.sleep(500)

    def set_listitem_details(self, cur_listitem, content_type, prefix):
        '''set the window properties based on the current listitem'''
        try:
            if cur_listitem in self.listitem_details:
                # data already in memory
                all_props = self.listitem_details[cur_listitem]
            else:
                # skip if another lookup for the same listitem is already in progress...
                if self.lookup_busy.get(cur_listitem) or self.exit:
                    return
                self.lookup_busy[cur_listitem] = True

                # clear all window props, do this delayed to prevent flickering of the screen
                thread.start_new_thread(self.delayed_flush, (cur_listitem,))

                # wait if we already have more than 5 items in the queue
                while len(self.lookup_busy) > 5:
                    xbmc.sleep(100)
                    if self.exit or cur_listitem != self.last_listitem:
                        self.lookup_busy.pop(cur_listitem, None)
                        return

                # prefer listitem's contenttype over container's contenttype
                dbtype = xbmc.getInfoLabel("%sListItem.DBTYPE" % prefix)
                if not dbtype:
                    dbtype = xbmc.getInfoLabel("%sListItem.Property(DBTYPE)" % prefix)
                if dbtype:
                    content_type = dbtype + "s"

                # collect details from listitem
                details = self.get_listitem_details(content_type, prefix)
                
                if self.exit:
                    return

                # music content
                if content_type in ["albums", "artists", "songs"] and self.enable_musicart:
                    details = self.metadatautils.extend_dict(details, self.metadatautils.get_music_artwork(
                        details["artist"], details["album"], details["title"], details["discnumber"]))
                # moviesets
                elif details["path"].startswith("videodb://movies/sets/") and details["dbid"]:
                    details = self.metadatautils.extend_dict(
                        details, self.metadatautils.get_moviesetdetails(
                            details["title"], details["dbid"]), ["year"])
                    content_type = "sets"
                # video content
                elif content_type in ["movies", "setmovies", "tvshows", "seasons", "episodes", "musicvideos"]:

                    # get imdb and tvdbid
                    details["imdbnumber"], tvdbid = self.metadatautils.get_imdbtvdb_id(
                        details["title"], content_type,
                        details["year"], details["imdbnumber"], details["tvshowtitle"])
                        
                    if self.exit:
                        return

                    # generic video properties (studio, streamdetails, omdb, top250)
                    details = merge_dict(details,
                                         self.get_directors_writers_cast(details["director"], details["writer"], details["cast"]))
                    if self.enable_extrafanart:
                        log_msg("skin.helper.service: extrafanart", xbmc.LOGINFO)
                        if not details["filenameandpath"]:
                            details["filenameandpath"] = details["path"]
                        if "plugin://" in details["path"]:
                            efa = self.metadatautils.get_extrafanart(details["path"])
                        if "plugin://" not in details["path"]:
                            if content_type in ["movie", "movies", "episodes", "episode", "musicvideos"]:
                                efa = self.metadatautils.get_extrafanart(details["path"])
                            if content_type in ["tvshows", "tvshow", "seasons", "season"]:
                                efa = self.metadatautils.get_extrafanart(details["filenameandpath"])
                        if efa:
                            details["art"] = merge_dict(details["art"], efa["art"])
                    if self.enable_extraposter:
                        if not details["filenameandpath"]:
                            details["filenameandpath"] = details["path"]
                        if "plugin://" in details["path"]:
                            efa = self.metadatautils.get_extraposter(details["path"])
                        if "plugin://" not in details["path"]:
                            if content_type in ["movie", "movies", "episodes", "episode", "musicvideos"]:
                                efa = self.metadatautils.get_extraposter(details["path"])
                            if content_type in ["tvshows", "tvshow", "seasons", "season"]:
                                efa = self.metadatautils.get_extraposter(details["filenameandpath"])
                        if efa:
                            details["art"] = merge_dict(details["art"], efa["art"])
                    if self.exit:
                        return

                    details = merge_dict(details, self.metadatautils.get_duration(details["duration"]))
                    details = merge_dict(details, self.get_genres(details["genre"]))
                    details = merge_dict(details, self.metadatautils.get_studio_logo(details["studio"]))
                    details = merge_dict(details, self.metadatautils.get_omdb_info(details["imdbnumber"]))
                    details = merge_dict(details, self.metadatautils.get_trakt_info(details["imdbnumber"]))
                    details = merge_dict(
                        details, self.get_streamdetails(
                            details["dbid"], details["path"], content_type))
                    details = merge_dict(details, self.metadatautils.get_top250_rating(details["imdbnumber"]))

                    if self.exit:
                        return

                    # tvshows-only properties (tvdb)
                    if content_type in ["tvshows", "seasons", "episodes"]:
                        details = merge_dict(
                            details, self.metadatautils.get_tvdb_details(
                                details["imdbnumber"], tvdbid))
                     # tvshows-only properties (metacritic)
                    if content_type in ["movie", "movies", "tvshow", "tvshows"]:
                        details = merge_dict(details, self.metadatautils.get_metacritic_info((details["title"]), content_type))
                    if content_type in ["movie", "movies", "tvshow", "tvshows"]:
                        details = merge_dict(details, self.metadatautils.get_tunes_info((details["title"]), content_type, details["year"]))
                    # movies-only properties (tmdb, animated art)
                    if content_type in ["movies", "setmovies", "tvshows"]:
                        details = merge_dict(details, self.metadatautils.get_tmdb_details(details["imdbnumber"]))
                        if details["imdbnumber"] and self.enable_animatedart:
                            details = self.metadatautils.extend_dict(
                                details, self.metadatautils.get_animated_artwork(
                                    details["imdbnumber"]))

                    if self.exit:
                        return

                    # extended art
                    if self.enable_extendedart:
                        tmdbid = details.get("tmdb_id", "")
                        details = self.metadatautils.extend_dict(
                            details, self.metadatautils.get_extended_artwork(
                                details["imdbnumber"], tvdbid, tmdbid, content_type), [
                                "posters", "clearlogos", "banners", "discarts", "cleararts", "characterarts"])
                # monitor listitem props when PVR is active
                elif content_type in ["tvchannels", "tvrecordings", "channels", "recordings", "timers", "tvtimers"]:
                    details = self.get_pvr_artwork(details, prefix)

                # process all properties
                all_props = prepare_win_props(details)
                if "sets" not in content_type:
                    self.listitem_details[cur_listitem] = all_props

                self.lookup_busy.pop(cur_listitem, None)

            if cur_listitem == self.last_listitem:
                self.set_win_props(all_props)
        except Exception as exc:
            log_exception(__name__, exc)
            self.lookup_busy.pop(cur_listitem, None)

    def delayed_flush(self, cur_listitem):
        '''flushes existing properties when it takes too long to grab the new ones'''
        xbmc.sleep(500)
        if cur_listitem == self.last_listitem and cur_listitem in self.lookup_busy:
            self.reset_win_props()

    def do_background_work(self):
        '''stuff that's processed in the background'''
        try:
            if self.exit:
                return
            log_msg("Started Background worker...")
            self.set_generic_props()
            self.listitem_details = {}
            if self.exit:
                return
            self.cache.check_cleanup()
            log_msg("Ended Background worker...")
        except Exception as exc:
            log_exception(__name__, exc)

    def set_generic_props(self):
        '''set some generic window props with item counts'''
        # GET TOTAL ADDONS COUNT
        addons_count = len(kodi_json('Addons.GetAddons'))
        self.win.setProperty("SkinHelper.TotalAddons", "%s" % addons_count)

        addontypes = []
        addontypes.append(("executable", "SkinHelper.TotalProgramAddons"))
        addontypes.append(("video", "SkinHelper.TotalVideoAddons"))
        addontypes.append(("audio", "SkinHelper.TotalAudioAddons"))
        addontypes.append(("image", "SkinHelper.TotalPicturesAddons"))
        for addontype in addontypes:
            media_array = kodi_json('Addons.GetAddons', {"content": addontype[0]})
            self.win.setProperty(addontype[1], str(len(media_array)))

        # GET FAVOURITES COUNT
        favs = kodi_json('Favourites.GetFavourites')
        if favs:
            self.win.setProperty("SkinHelper.TotalFavourites", "%s" % len(favs))
            
        if self.exit:
            return

        # GET TV CHANNELS COUNT
        if getCondVisibility("Pvr.HasTVChannels"):
            tv_channels = kodi_json('PVR.GetChannels', {"channelgroupid": "alltv"})
            self.win.setProperty("SkinHelper.TotalTVChannels", "%s" % len(tv_channels))
            
        if self.exit:
            return

        # GET MOVIE SETS COUNT
        movieset_movies_count = 0
        moviesets = kodi_json('VideoLibrary.GetMovieSets')
        for item in moviesets:
            for item in kodi_json('VideoLibrary.GetMovieSetDetails', {"setid": item["setid"]}):
                movieset_movies_count += 1
        self.win.setProperty("SkinHelper.TotalMovieSets", "%s" % len(moviesets))
        self.win.setProperty("SkinHelper.TotalMoviesInSets", "%s" % movieset_movies_count)
        
        if self.exit:
            return

        # GET RADIO CHANNELS COUNT
        if getCondVisibility("Pvr.HasRadioChannels"):
            radio_channels = kodi_json('PVR.GetChannels', {"channelgroupid": "allradio"})
            self.win.setProperty("SkinHelper.TotalRadioChannels", "%s" % len(radio_channels))

    def reset_win_props(self):
        '''reset all window props set by the script...'''
        self.metadatautils.process_method_on_list(self.win.clearProperty, iter(list(self.all_window_props.keys())))
        self.all_window_props = {}

    def set_win_prop(self, prop_tuple):
        '''sets a window property based on the given key-value'''
        key = prop_tuple[0]
        value = prop_tuple[1]
        if (key not in self.all_window_props) or (key in self.all_window_props and self.all_window_props[key] != value):
            self.all_window_props[key] = value
            self.win.setProperty(key, value)

    def set_win_props(self, prop_tuples):
        '''set multiple window properties from list of tuples'''
        self.metadatautils.process_method_on_list(self.set_win_prop, prop_tuples)
        # cleanup remaining properties
        new_keys = [item[0] for item in prop_tuples]
        for key, value in list(self.all_window_props.items()):
            if value and key not in new_keys:
                self.all_window_props[key] = ""
                self.win.clearProperty(key)

    def set_content_header(self, content_type):
        '''sets a window propery which can be used as headertitle'''
        self.win.clearProperty("SkinHelper.ContentHeader")
        itemscount = xbmc.getInfoLabel("Container.NumItems")
        if itemscount:
            if xbmc.getInfoLabel("Container.ListItemNoWrap(0).Label").startswith(
                    "*") or xbmc.getInfoLabel("Container.ListItemNoWrap(1).Label").startswith("*"):
                itemscount = int(itemscount) - 1
            headerprefix = ""
            if content_type == "movies":
                headerprefix = xbmc.getLocalizedString(36901)
            elif content_type == "tvshows":
                headerprefix = xbmc.getLocalizedString(36903)
            elif content_type == "seasons":
                headerprefix = xbmc.getLocalizedString(36905)
            elif content_type == "episodes":
                headerprefix = xbmc.getLocalizedString(36907)
            elif content_type == "sets":
                headerprefix = xbmc.getLocalizedString(36911)
            elif content_type == "albums":
                headerprefix = xbmc.getLocalizedString(36919)
            elif content_type == "songs":
                headerprefix = xbmc.getLocalizedString(36921)
            elif content_type == "artists":
                headerprefix = xbmc.getLocalizedString(36917)
            if headerprefix:
                self.win.setProperty("SkinHelper.ContentHeader", "%s %s" % (itemscount, headerprefix))

    @staticmethod
    def get_genres(genres):
        '''get formatted genre string from actual genre'''
        details = {}
        if not isinstance(genres, list):
            genres = genres.split(" / ")
        details['genres'] = "[CR]".join(genres)
        for count, genre in enumerate(genres):
            details["genre.%s" % count] = genre
        return details

    @staticmethod
    def get_directors_writers_cast(director, writer, cast):
        '''get a formatted string with directors/writers/cast from the actual string'''
        directors = director.split(" / ")
        writers = writer.split(" / ")
        cast_list = cast.replace("\n", ", ")                                    
        return {
            'Directors': "[CR]".join(directors),
            'Writers': "[CR]".join(writers),
            'CastListing': cast_list}                        

    def get_listitem_details(self, content_type, prefix):
        '''collect all listitem properties/values we need'''
        listitem_details = {"art": {}}

        # basic properties
        for prop in ["dbtype", "dbid", "imdbnumber"]:
            propvalue = try_decode(xbmc.getInfoLabel('$INFO[%sListItem.%s]' % (prefix, prop)))
            if not propvalue or propvalue == "-1":
                propvalue = try_decode(xbmc.getInfoLabel('$INFO[%sListItem.Property(%s)]' % (prefix, prop)))
            listitem_details[prop] = propvalue

        # generic properties
        props = ["label", "title", "filenameandpath", "year", "genre", "path", "folderpath",
                 "duration", "plot", "plotoutline", "label2", "icon", "thumb"]
        # properties for media items
        if content_type in ["movies", "tvshows", "seasons", "episodes", "musicvideos", "setmovies"]:
            props += ["studio", "tvshowtitle", "premiered", "director", "writer",
                      "firstaired", "tagline", "rating", "season", "episode", "cast"]
        # properties for music items
        elif content_type in ["musicvideos", "artists", "albums", "songs"]:
            props += ["artist", "album", "rating", "albumartist", "discnumber"]
        # properties for pvr items
        elif content_type in ["tvchannels", "tvrecordings", "channels", "recordings", "timers", "tvtimers"]:
            props += ["channel", "channelname"]

        for prop in props:
            if self.exit:
                break
            propvalue = try_decode(xbmc.getInfoLabel('$INFO[%sListItem.%s]' % (prefix, prop)))
            listitem_details[prop] = propvalue

        # artwork properties
        artprops = ["fanart", "poster", "clearlogo", "clearart",
                    "landscape", "thumb", "banner", "discart", "characterart"]
        for prop in artprops:
            if self.exit:
                break
            propvalue = try_decode(xbmc.getInfoLabel('$INFO[%sListItem.Art(%s)]' % (prefix, prop)))
            if not propvalue:
                propvalue = try_decode(xbmc.getInfoLabel('$INFO[%sListItem.Art(tvshow.%s)]' % (prefix, prop)))
            if propvalue:
                listitem_details["art"][prop] = propvalue

        # fix for folderpath
        if not listitem_details.get("path") and "folderpath" in listitem_details:
            listitem_details["path"] = listitem_details["folderpath"]
        # fix for thumb
        if "thumb" not in listitem_details["art"] and "thumb" in listitem_details:
            listitem_details["art"]["thumb"] = listitem_details["thumb"]
        if "fanart" not in listitem_details["art"] and "fanart" in listitem_details:
            listitem_details["art"]["fanart"] = listitem_details["fanart"]
        return listitem_details

    def get_streamdetails(self, li_dbid, li_path, content_type):
        '''get the streamdetails for the current video'''
        details = {}
        if li_dbid and content_type in ["movies", "episodes",
                                        "musicvideos"] and not li_path.startswith("videodb://movies/sets/"):
            details = self.metadatautils.get_streamdetails(li_dbid, content_type)
        return details

    def set_forcedview(self, content_type):
        '''helper to force the view in certain conditions'''
        if self.enable_forcedviews:
            cur_forced_view = xbmc.getInfoLabel("Skin.String(SkinHelper.ForcedViews.%s)" % content_type)
            if getCondVisibility(
                    "Control.IsVisible(%s) | String.IsEmpty(Container.Viewmode) | System.HasModalDialog | System.HasVisibleModalDialog" % cur_forced_view):
                # skip if the view is already visible or if we're not in an actual media window
                return
            if (content_type and cur_forced_view and cur_forced_view != "None" and not
                    getCondVisibility("Window.IsActive(MyPvrGuide.xml)")):
                self.win.setProperty("SkinHelper.ForcedView", cur_forced_view)
                count = 0
                while not getCondVisibility("Control.HasFocus(%s)" % cur_forced_view):
                    xbmc.sleep(100)
                    xbmc.executebuiltin("Container.SetViewMode(%s)" % cur_forced_view)
                    xbmc.executebuiltin("SetFocus(%s)" % cur_forced_view)
                    count += 1
                    if count == 50 or self.exit:
                        break
            else:
                self.win.clearProperty("SkinHelper.ForcedView")
        else:
            self.win.clearProperty("SkinHelper.ForcedView")

    def get_pvr_artwork(self, listitem, prefix):
        '''get pvr artwork from artwork module'''
        if self.enable_pvrart:
            if getCondVisibility("%sListItem.IsFolder" % prefix) and not listitem[
                    "channelname"] and not listitem["title"]:
                listitem["title"] = listitem["label"]
            listitem = self.metadatautils.extend_dict(
                listitem, self.metadatautils.get_pvr_artwork(
                    listitem["title"],
                    listitem["channelname"],
                    listitem["genre"]), ["title", "genre", "genres", "thumb"])
        if listitem["channelname"]:
            next_listitem = try_decode(xbmc.getInfoLabel(
                "$INFO[%sListItem.NextTitle]" % prefix))
            listitem["art"]["NextTitle"] = self.metadatautils.google.search_image(next_listitem)    
        # pvr channellogo
        if listitem["channelname"]:
            listitem["art"]["ChannelLogo"] = self.metadatautils.get_channellogo(listitem["channelname"])
        elif listitem.get("pvrchannel"):
            listitem["art"]["ChannelLogo"] = self.metadatautils.get_channellogo(listitem["pvrchannel"])
        return listitem
