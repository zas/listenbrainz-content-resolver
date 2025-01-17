import datetime
import os
from uuid import UUID

import libsonic

from lb_content_resolver.database import Database
from lb_content_resolver.model.database import db
import config


class SubsonicDatabase(Database):
    ''' 
    Add subsonic sync capabilities to the Database
    '''

    MAX_ALBUMS_PER_CALL = 500

    def __init__(self, index_dir):
        Database.__init__(self, index_dir)

    def sync(self):
        """
            Scan the subsonic client specified in config.py
        """

        # Keep some stats
        self.total = 0
        self.added = 0
        self.removed = 0
        self.updated = 0

        self.open_db()
        self.run_sync()
        self.close_db()

        print("Checked %s tracks:" % self.total)
        print("  %5d tracks added" % self.added)
        print("  %5d tracks updated" % self.updated)
        print("  %5d tracks removed" % self.removed)

    def run_sync(self):
        """
            Perform the sync between the local collection and the subsonic one.
        """

        print("Connect to subsonic..")
        conn = libsonic.Connection(config.SUBSONIC_HOST, config.SUBSONIC_USER, config.SUBSONIC_PASSWORD, config.SUBSONIC_PORT)

        cursor = db.connection().cursor()

        print("Fetch recordings")
        album_count = 0
        while True:
            recordings = []
            albums_this_batch = 0
            albums = conn.getAlbumList(ltype="alphabeticalByArtist", size=self.MAX_ALBUMS_PER_CALL, offset=album_count)

            for album in albums["albumList"]["album"]:
                album_count += 1
                albums_this_batch += 1

                album_info = conn.getAlbumInfo2(id=album["id"])
                try:
                    album_mbid = album_info["albumInfo"]["musicBrainzId"]
                except KeyError:
                    print("subsonic album '%s' by '%s' has no MBID" % (album["album"], album["artist"]))
                    continue

                cursor.execute(
                    """SELECT recording.id
                                       , track_num
                                       , COALESCE(disc_num, 1)
                                    FROM recording
                                   WHERE release_mbid = ?""", (album_mbid, ))

                # create index on (track_num, disc_num)
                release_tracks = {(row[1], row[2]): row[0] for row in cursor.fetchall()}

                album_info = conn.getAlbum(id=album["id"])

                if len(release_tracks) == 0:
                    print("For album %s" % album_mbid)
                    print("loaded %d of %d expected tracks from DB." % (len(release_tracks), len(album_info["album"]["song"])))

                print("album '%s' by '%s'" % (album["album"], album["artist"]))
                if "song" not in album_info["album"]:
                    print("No songs returned")
                else:
                    for song in album_info["album"]["song"]:

                        if (song["track"], song.get("discNumber", 1)) in release_tracks:
                            recordings.append((release_tracks[(song["track"], song["discNumber"])], song["id"]))
                        else:
                            print("Song not matched: ", song["title"])
                            continue

            self.update_recordings(recordings)

            print("fetched %d releases" % albums_this_batch)
            if albums_this_batch < self.MAX_ALBUMS_PER_CALL:
                break

    def update_recordings(self, recordings):
        """
            Given a list of recording_subsonic records, update the DB.
            Updates recording_id, subsonic_id, last_update
        """

        recordings = [(r[0], r[1], datetime.datetime.now()) for r in recordings]

        cursor = db.connection().cursor()
        cursor.executemany(
            """INSERT INTO recording_subsonic (recording_id, subsonic_id, last_updated)
                                    VALUES (?, ?, ?)
                 ON CONFLICT DO UPDATE SET recording_id = excluded.recording_id
                                         , subsonic_id = excluded.subsonic_id
                                         , last_updated = excluded.last_updated""", recordings)

    def upload_playlist(self, jspf):
        """
            Given a JSPF playlist, upload the playlist to the subsonic API.
        """

        conn = libsonic.Connection(config.SUBSONIC_HOST, config.SUBSONIC_USER, config.SUBSONIC_PASSWORD, config.SUBSONIC_PORT)

        song_ids = [
            track["extension"]["https://musicbrainz.org/doc/jspf#track"]["additional_metadata"]["subsonic_identifier"][33:]
            for track in jspf["playlist"]["track"]
        ]
        name = jspf["playlist"]["title"]
        conn.createPlaylist(name=name, songIds=song_ids)
