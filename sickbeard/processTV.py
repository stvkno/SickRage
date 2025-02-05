# Author: Nic Wolfe <nic@wolfeden.ca>
# URL: https://sickrage.github.io/
# Git: https://github.com/SickRage/SickRage.git
#
# This file is part of SickRage.
#
# SickRage is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# SickRage is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with SickRage.  If not, see <http://www.gnu.org/licenses/>.


import os
import stat

import sickbeard
from sickbeard import postProcessor
from sickbeard import db, helpers
from sickbeard import logger
from sickbeard.name_parser.parser import NameParser, InvalidNameException, InvalidShowException
from sickbeard import common
from sickbeard import failedProcessor
from sickrage.helper.encoding import ek, ss
from sickrage.helper.exceptions import EpisodePostProcessingFailedException, ex, FailedPostProcessingFailedException

from unrar2 import RarFile
from unrar2.rar_exceptions import FileOpenError
from unrar2.rar_exceptions import ArchiveHeaderBroken
from unrar2.rar_exceptions import InvalidRARArchive
from unrar2.rar_exceptions import InvalidRARArchiveUsage
from unrar2.rar_exceptions import IncorrectRARPassword

import shutil
import shutil_custom

shutil.copyfile = shutil_custom.copyfile_custom


class ProcessResult(object):
    def __init__(self):
        self.result = True
        self.output = ''
        self.missedfiles = []
        self.aggresult = True


def delete_folder(folder, check_empty=True):
    """
    Removes a folder from the filesystem

    :param folder: Path to folder to remove
    :param check_empty: Boolean, check if the folder is empty before removing it, defaults to True
    :return: True on success, False on failure
    """

    # check if it's a folder
    if not ek(os.path.isdir, folder):
        return False

    # check if it isn't TV_DOWNLOAD_DIR
    if sickbeard.TV_DOWNLOAD_DIR:
        if helpers.real_path(folder) == helpers.real_path(sickbeard.TV_DOWNLOAD_DIR):
            return False

    # check if it's empty folder when wanted checked
    if check_empty:
        check_files = ek(os.listdir, folder)
        if check_files:
            logger.log(u"Not deleting folder " + folder + " found the following files: " + str(check_files), logger.INFO)
            return False

        try:
            logger.log(u"Deleting folder (if it's empty): " + folder)
            os.rmdir(folder)
        except (OSError, IOError), e:
            logger.log(u"Warning: unable to delete folder: " + folder + ": " + ex(e), logger.WARNING)
            return False
    else:
        try:
            logger.log(u"Deleting folder: " + folder)
            shutil.rmtree(folder)
        except (OSError, IOError), e:
            logger.log(u"Warning: unable to delete folder: " + folder + ": " + ex(e), logger.WARNING)
            return False

    return True


def delete_files(processPath, notwantedFiles, result, force=False):
    """
    Remove files from filesystem

    :param processPath: path to process
    :param notwantedFiles: files we do not want
    :param result: Processor results
    :param force: Boolean, force deletion, defaults to false
    """

    if not result.result and force:
        result.output += logHelper(u"Forcing deletion of files, even though last result was not success", logger.DEBUG)
    elif not result.result:
        return

    # Delete all file not needed
    for cur_file in notwantedFiles:

        cur_file_path = ek(os.path.join, processPath, cur_file)

        if not ek(os.path.isfile, cur_file_path):
            continue  # Prevent error when a notwantedfiles is an associated files

        result.output += logHelper(u"Deleting file " + cur_file, logger.DEBUG)

        # check first the read-only attribute
        file_attribute = ek(os.stat, cur_file_path)[0]
        if not file_attribute & stat.S_IWRITE:
            # File is read-only, so make it writeable
            result.output += logHelper(u"Changing ReadOnly Flag for file " + cur_file, logger.DEBUG)
            try:
                ek(os.chmod, cur_file_path, stat.S_IWRITE)
            except OSError, e:
                result.output += logHelper(u"Cannot change permissions of " + cur_file_path + ': ' + ex(e), logger.DEBUG)
        try:
            ek(os.remove, cur_file_path)
        except OSError, e:
            result.output += logHelper(u"Unable to delete file " + cur_file + ': ' + str(e.strerror), logger.DEBUG)


def logHelper(logMessage, logLevel=logger.INFO):
    logger.log(logMessage, logLevel)
    return logMessage + u"\n"


def processDir(dirName, nzbName=None, process_method=None, force=False, is_priority=None, delete_on=False, failed=False, proc_type="auto"):
    """
    Scans through the files in dirName and processes whatever media files it finds

    :param dirName: The folder name to look in
    :param nzbName: The NZB name which resulted in this folder being downloaded
    :param force: True to postprocess already postprocessed files
    :param failed: Boolean for whether or not the download failed
    :param proc_type: Type of postprocessing auto or manual
    """

    result = ProcessResult()

    result.output += logHelper(u"Processing folder " + dirName, logger.DEBUG)

    result.output += logHelper(u"TV_DOWNLOAD_DIR: " + sickbeard.TV_DOWNLOAD_DIR, logger.DEBUG)
    postpone = False
    # if they passed us a real dir then assume it's the one we want
    if ek(os.path.isdir, dirName):
        dirName = ek(os.path.realpath, dirName)

    # if the client and SickRage are not on the same machine translate the Dir in a network dir
    elif sickbeard.TV_DOWNLOAD_DIR and ek(os.path.isdir, sickbeard.TV_DOWNLOAD_DIR) \
            and ek(os.path.normpath, dirName) != ek(os.path.normpath, sickbeard.TV_DOWNLOAD_DIR):
        dirName = ek(os.path.join, sickbeard.TV_DOWNLOAD_DIR, ek(os.path.abspath, dirName).split(os.path.sep)[-1])
        result.output += logHelper(u"Trying to use folder " + dirName, logger.DEBUG)

    # if we didn't find a real dir then quit
    if not ek(os.path.isdir, dirName):
        result.output += logHelper(
            u"Unable to figure out what folder to process. If your downloader and SickRage aren't on the same PC make sure you fill out your TV download dir in the config.",
            logger.DEBUG)
        return result.output

    path, dirs, files = get_path_dir_files(dirName, nzbName, proc_type)

    files = [x for x in files if helpers.notTorNZBFile(x)]
    SyncFiles = [x for x in files if helpers.isSyncFile(x)]

    # Don't post process if files are still being synced and option is activated
    if SyncFiles and sickbeard.POSTPONE_IF_SYNC_FILES:
        postpone = True

    nzbNameOriginal = nzbName

    if not postpone:
        result.output += logHelper(u"PostProcessing Path: " + path, logger.INFO)
        result.output += logHelper(u"PostProcessing Dirs: " + str(dirs), logger.DEBUG)

        rarFiles = [x for x in files if helpers.isRarFile(x)]
        rarContent = unRAR(path, rarFiles, force, result)
        files += rarContent
        videoFiles = [x for x in files if helpers.isMediaFile(x)]
        videoInRar = [x for x in rarContent if helpers.isMediaFile(x)]

        result.output += logHelper(u"PostProcessing Files: " + str(files), logger.DEBUG)
        result.output += logHelper(u"PostProcessing VideoFiles: " + str(videoFiles), logger.DEBUG)
        result.output += logHelper(u"PostProcessing RarContent: " + str(rarContent), logger.DEBUG)
        result.output += logHelper(u"PostProcessing VideoInRar: " + str(videoInRar), logger.DEBUG)

        # If nzbName is set and there's more than one videofile in the folder, files will be lost (overwritten).
        if len(videoFiles) >= 2:
            nzbName = None

        if not process_method:
            process_method = sickbeard.PROCESS_METHOD

        result.result = True

        # Don't Link media when the media is extracted from a rar in the same path
        if process_method in ('hardlink', 'symlink') and videoInRar:
            process_media(path, videoInRar, nzbName, 'move', force, is_priority, result)
            delete_files(path, rarContent, result)
            for video in set(videoFiles) - set(videoInRar):
                process_media(path, [video], nzbName, process_method, force, is_priority, result)
        elif sickbeard.DELRARCONTENTS and videoInRar:
            process_media(path, videoInRar, nzbName, process_method, force, is_priority, result)
            delete_files(path, rarContent, result, True)
            for video in set(videoFiles) - set(videoInRar):
                process_media(path, [video], nzbName, process_method, force, is_priority, result)
        else:
            for video in videoFiles:
                process_media(path, [video], nzbName, process_method, force, is_priority, result)

    else:
        result.output += logHelper(u"Found temporary sync files, skipping post processing for folder " + str(path))
        result.output += logHelper(u"Sync Files: " + str(SyncFiles) + " in path: " + path)
        result.missedfiles.append(path + " : Syncfiles found")

    # Process Video File in all TV Subdir
    for curDir in [x for x in dirs if validateDir(path, x, nzbNameOriginal, failed, result)]:

        result.result = True

        for processPath, _, fileList in ek(os.walk, ek(os.path.join, path, curDir), topdown=False):

            if not validateDir(path, processPath, nzbNameOriginal, failed, result):
                continue

            postpone = False

            SyncFiles = [x for x in fileList if helpers.isSyncFile(x)]

            # Don't post process if files are still being synced and option is activated
            if SyncFiles and sickbeard.POSTPONE_IF_SYNC_FILES:
                postpone = True

            if not postpone:
                rarFiles = [x for x in fileList if helpers.isRarFile(x)]
                rarContent = unRAR(processPath, rarFiles, force, result)
                fileList = set(fileList + rarContent)
                videoFiles = [x for x in fileList if helpers.isMediaFile(x)]
                videoInRar = [x for x in rarContent if helpers.isMediaFile(x)]
                notwantedFiles = [x for x in fileList if x not in videoFiles]
                if notwantedFiles:
                    result.output += logHelper(u"Found unwanted files: " + str(notwantedFiles), logger.DEBUG)

                # Don't Link media when the media is extracted from a rar in the same path
                if process_method in ('hardlink', 'symlink') and videoInRar:
                    process_media(processPath, videoInRar, nzbName, 'move', force, is_priority, result)
                    process_media(processPath, set(videoFiles) - set(videoInRar), nzbName, process_method, force,
                                  is_priority, result)
                    delete_files(processPath, rarContent, result)
                elif sickbeard.DELRARCONTENTS and videoInRar:
                    process_media(processPath, videoInRar, nzbName, process_method, force, is_priority, result)
                    process_media(processPath, set(videoFiles) - set(videoInRar), nzbName, process_method, force,
                                  is_priority, result)
                    delete_files(processPath, rarContent, result, True)
                else:
                    process_media(processPath, videoFiles, nzbName, process_method, force, is_priority, result)

                    # Delete all file not needed
                    if process_method != "move" or not result.result \
                            or (proc_type == "manual" and not delete_on):  # Avoid to delete files if is Manual PostProcessing
                        continue

                    delete_files(processPath, notwantedFiles, result)

                    if (not sickbeard.NO_DELETE or proc_type == "manual") and process_method == "move" and \
                        ek(os.path.normpath, processPath) != ek(os.path.normpath, sickbeard.TV_DOWNLOAD_DIR):
                        if delete_folder(processPath, check_empty=True):
                            result.output += logHelper(u"Deleted folder: " + processPath, logger.DEBUG)
            else:
                result.output += logHelper(u"Found temporary sync files, skipping post processing for folder: " + str(processPath))
                result.output += logHelper(u"Sync Files: " + str(SyncFiles) + " in path: " + processPath)
                result.missedfiles.append(processPath + " : Syncfiles found")

    if result.aggresult:
        result.output += logHelper(u"Processing completed")
        if result.missedfiles:
            result.output += logHelper(u"I did encounter some unprocessable items: ")
            for missedfile in result.missedfiles:
                result.output += logHelper(u"[" + missedfile + "]")
    else:
        result.output += logHelper(u"Problem(s) during processing, failed the following files/folders:  ", logger.WARNING)
        for missedfile in result.missedfiles:
            result.output += logHelper(u"[" + missedfile + "]", logger.WARNING)

    return result.output


def validateDir(path, dirName, nzbNameOriginal, failed, result):
    """
    Check if directory is valid for processing

    :param path: Path to use
    :param dirName: Directory to check
    :param nzbNameOriginal: Original NZB name
    :param failed: Previously failed objects
    :param result: Previous results
    :return: True if dir is valid for processing, False if not
    """

    IGNORED_FOLDERS = ['.AppleDouble', '.@__thumb', '@eaDir']
    folder_name = ek(os.path.basename, dirName)
    if folder_name in IGNORED_FOLDERS:
        return False

    result.output += logHelper(u"Processing folder " + dirName, logger.DEBUG)

    if folder_name.startswith('_FAILED_'):
        result.output += logHelper(u"The directory name indicates it failed to extract.", logger.DEBUG)
        failed = True
    elif folder_name.startswith('_UNDERSIZED_'):
        result.output += logHelper(u"The directory name indicates that it was previously rejected for being undersized.", logger.DEBUG)
        failed = True
    elif folder_name.upper().startswith('_UNPACK'):
        result.output += logHelper(u"The directory name indicates that this release is in the process of being unpacked.", logger.DEBUG)
        result.missedfiles.append(dirName + " : Being unpacked")
        return False

    if failed:
        process_failed(os.path.join(path, dirName), nzbNameOriginal, result)
        result.missedfiles.append(dirName + " : Failed download")
        return False

    if helpers.is_hidden_folder(os.path.join(path, dirName)):
        result.output += logHelper(u"Ignoring hidden folder: " + dirName, logger.DEBUG)
        result.missedfiles.append(dirName + " : Hidden folder")
        return False

    # make sure the dir isn't inside a show dir
    myDB = db.DBConnection()
    sqlResults = myDB.select("SELECT * FROM tv_shows")

    for sqlShow in sqlResults:
        if dirName.lower().startswith(ek(os.path.realpath, sqlShow["location"]).lower() + os.sep) or \
            dirName.lower() == ek(os.path.realpath, sqlShow["location"]).lower():

            result.output += logHelper(
                u"Cannot process an episode that's already been moved to its show dir, skipping " + dirName,
                logger.WARNING)
            return False

    # Get the videofile list for the next checks
    allFiles = []
    allDirs = []
    for _, processdir, fileList in ek(os.walk, ek(os.path.join, path, dirName), topdown=False):
        allDirs += processdir
        allFiles += fileList

    videoFiles = [x for x in allFiles if helpers.isMediaFile(x)]
    allDirs.append(dirName)

    # check if the dir have at least one tv video file
    for video in videoFiles:
        try:
            NameParser().parse(video, cache_result=False)
            return True
        except (InvalidNameException, InvalidShowException):
            pass

    for proc_dir in allDirs:
        try:
            NameParser().parse(proc_dir, cache_result=False)
            return True
        except (InvalidNameException, InvalidShowException):
            pass

    if sickbeard.UNPACK:
        # Search for packed release
        packedFiles = [x for x in allFiles if helpers.isRarFile(x)]

        for packed in packedFiles:
            try:
                NameParser().parse(packed, cache_result=False)
                return True
            except (InvalidNameException, InvalidShowException):
                pass

    result.output += logHelper(dirName + " : No processable items found in folder", logger.DEBUG)
    return False

def unRAR(path, rarFiles, force, result):
    """
    Extracts RAR files

    :param path: Path to look for files in
    :param rarFiles: Names of RAR files
    :param force: process currently processing items
    :param result: Previous results
    :return: List of unpacked file names
    """

    unpacked_files = []

    if sickbeard.UNPACK and rarFiles:

        result.output += logHelper(u"Packed Releases detected: " + str(rarFiles), logger.DEBUG)

        for archive in rarFiles:

            result.output += logHelper(u"Unpacking archive: " + archive, logger.DEBUG)

            try:
                rar_handle = RarFile(os.path.join(path, archive))

                # Skip extraction if any file in archive has previously been extracted
                skip_file = False
                for file_in_archive in [os.path.basename(x.filename) for x in rar_handle.infolist() if not x.isdir]:
                    if already_postprocessed(path, file_in_archive, force, result):
                        result.output += logHelper(
                            u"Archive file already post-processed, extraction skipped: " + file_in_archive,
                            logger.DEBUG)
                        skip_file = True
                        break

                if skip_file:
                    continue

                rar_handle.extract(path=path, withSubpath=False, overwrite=False)
                for x in rar_handle.infolist():
                    if not x.isdir:
                        basename = os.path.basename(x.filename)
                        if basename not in unpacked_files:
                            unpacked_files.append(basename)
                del rar_handle

            except ArchiveHeaderBroken as e:
                result.output += logHelper(u"Failed Unrar archive {0}: Unrar: Archive Header Broken".format(archive), logger.ERROR)
                result.result = False
                result.missedfiles.append(archive + " : Unpacking failed because the Archive Header is Broken")
                continue
            except IncorrectRARPassword:
                result.output += logHelper(u"Failed Unrar archive {0}: Unrar: Incorrect Rar Password".format(archive), logger.ERROR)
                result.result = False
                result.missedfiles.append(archive + " : Unpacking failed because of an Incorrect Rar Password")
                continue
            except FileOpenError:
                result.output += logHelper(u"Failed Unrar archive {0}: Unrar: File Open Error, check the parent folder and destination file permissions.".format(archive), logger.ERROR)
                result.result = False
                result.missedfiles.append(archive + " : Unpacking failed with a File Open Error (file permissions?)")
                continue
            except InvalidRARArchiveUsage:
                result.output += logHelper(u"Failed Unrar archive {0}: Unrar: Invalid Rar Archive Usage".format(archive), logger.ERROR)
                result.result = False
                result.missedfiles.append(archive + " : Unpacking Failed with Invalid Rar Archive Usage")
                continue
            except InvalidRARArchive:
                result.output += logHelper(u"Failed Unrar archive {0}: Unrar: Invalid Rar Archive".format(archive), logger.ERROR)
                result.result = False
                result.missedfiles.append(archive + " : Unpacking Failed with an Invalid Rar Archive Error")
                continue
            except Exception, e:
                result.output += logHelper(u"Failed Unrar archive " + archive + ': ' + ex(e), logger.ERROR)
                result.result = False
                result.missedfiles.append(archive + " : Unpacking failed for an unknown reason")
                continue

        result.output += logHelper(u"UnRar content: " + str(unpacked_files), logger.DEBUG)

    return unpacked_files


def already_postprocessed(dirName, videofile, force, result):
    """
    Check if we already post processed a file

    :param dirName: Directory a file resides in
    :param videofile: File name
    :param force: Force checking when already checking (currently unused)
    :param result: True if file is already postprocessed, False if not
    :return:
    """
    if force:
        return False

    # Avoid processing the same dir again if we use a process method <> move
    myDB = db.DBConnection()
    sqlResult = myDB.select("SELECT * FROM tv_episodes WHERE release_name = ?", [dirName])
    if sqlResult:
        # result.output += logHelper(u"You're trying to post process a dir that's already been processed, skipping", logger.DEBUG)
        return True

    else:
        sqlResult = myDB.select("SELECT * FROM tv_episodes WHERE release_name = ?", [videofile.rpartition('.')[0]])
        if sqlResult:
            # result.output += logHelper(u"You're trying to post process a video that's already been processed, skipping", logger.DEBUG)
            return True

        # Needed if we have downloaded the same episode @ different quality
        # But we need to make sure we check the history of the episode we're going to PP, and not others
        np = NameParser(dirName, tryIndexers=True)
        try:  # if it fails to find any info (because we're doing an unparsable folder (like the TV root dir) it will throw an exception, which we want to ignore
            parse_result = np.parse(dirName)
        except Exception:  # ignore the exception, because we kind of expected it, but create parse_result anyway so we can perform a check on it.
            parse_result = False


        search_sql = "SELECT tv_episodes.indexerid, history.resource FROM tv_episodes INNER JOIN history ON history.showid=tv_episodes.showid"  # This part is always the same
        search_sql += " WHERE history.season=tv_episodes.season and history.episode=tv_episodes.episode"
        # If we find a showid, a season number, and one or more episode numbers then we need to use those in the query
        if parse_result and (parse_result.show.indexerid and parse_result.episode_numbers and parse_result.season_number):
            search_sql += " and tv_episodes.showid = '" + str(parse_result.show.indexerid) + "' and tv_episodes.season = '" + str(parse_result.season_number) + "' and tv_episodes.episode = '" + str(parse_result.episode_numbers[0]) + "'"

        search_sql += " and tv_episodes.status IN (" + ",".join([str(x) for x in common.Quality.DOWNLOADED]) + ")"
        search_sql += " and history.resource LIKE ?"
        sqlResult = myDB.select(search_sql, ['%' + videofile])
        if sqlResult:
            # result.output += logHelper(u"You're trying to post process a video that's already been processed, skipping", logger.DEBUG)
            return True

    return False


def process_media(processPath, videoFiles, nzbName, process_method, force, is_priority, result):
    """
    Postprocess mediafiles

    :param processPath: Path to postprocess in
    :param videoFiles: Filenames to look for and postprocess
    :param nzbName: Name of NZB file related
    :param process_method: auto/manual
    :param force: Postprocess currently postprocessing file
    :param is_priority: Boolean, is this a priority download
    :param result: Previous results
    """

    processor = None
    for cur_video_file in videoFiles:
        cur_video_file_path = ek(os.path.join, processPath, cur_video_file)

        if already_postprocessed(processPath, cur_video_file, force, result):
            result.output += logHelper(u"Already Processed " + cur_video_file + " : Skipping", logger.DEBUG)
            continue

        try:
            processor = postProcessor.PostProcessor(cur_video_file_path, nzbName, process_method, is_priority)
            # This feature prevents PP for files that do not have subtitle associated with the video file        
            if sickbeard.POSTPONE_IF_NO_SUBS:
                associatedFiles = processor.list_associated_files(cur_video_file_path, subtitles_only=True)
                if not [associatedFile for associatedFile in associatedFiles if associatedFile[-3:] in common.subtitleExtensions]:
                    result.output += logHelper(u"No subtitles associated. Postponing the post-process of this file: %s" % cur_video_file, logger.DEBUG)
                    continue
                else:
                    result.output += logHelper(u"Found subtitles associated. Continuing the post-process of this file: %s" % cur_video_file) 
        
            result.result = processor.process()
            process_fail_message = ""
        except EpisodePostProcessingFailedException, e:
            result.result = False
            process_fail_message = ex(e)

        if processor:
            result.output += processor.log

        if result.result:
            result.output += logHelper(u"Processing succeeded for " + cur_video_file_path)
        else:
            result.output += logHelper(u"Processing failed for " + cur_video_file_path + ": " + process_fail_message, logger.WARNING)
            result.missedfiles.append(cur_video_file_path + " : Processing failed: " + process_fail_message)
            result.aggresult = False


def get_path_dir_files(dirName, nzbName, proc_type):
    """
    Get files in a path

    :param dirName: Directory to start in
    :param nzbName: NZB file, if present
    :param proc_type: auto/manual
    :return: a tuple of (path,dirs,files)
    """
    path = ""
    dirs = []
    files = []

    if dirName == sickbeard.TV_DOWNLOAD_DIR and not nzbName or proc_type == "manual":  # Scheduled Post Processing Active
        # Get at first all the subdir in the dirName
        for path, dirs, files in ek(os.walk, dirName):
            break
    else:
        path, dirs = ek(os.path.split, dirName)  # Script Post Processing
        if not nzbName is None and not nzbName.endswith('.nzb') and os.path.isfile(
                os.path.join(dirName, nzbName)):  # For single torrent file without Dir
            dirs = []
            files = [os.path.join(dirName, nzbName)]
        else:
            dirs = [dirs]
            files = []

    return path, dirs, files


def process_failed(dirName, nzbName, result):
    """Process a download that did not complete correctly"""

    if sickbeard.USE_FAILED_DOWNLOADS:
        processor = None

        try:
            processor = failedProcessor.FailedProcessor(dirName, nzbName)
            result.result = processor.process()
            process_fail_message = ""
        except FailedPostProcessingFailedException, e:
            result.result = False
            process_fail_message = ex(e)

        if processor:
            result.output += processor.log

        if sickbeard.DELETE_FAILED and result.result:
            if delete_folder(dirName, check_empty=False):
                result.output += logHelper(u"Deleted folder: " + dirName, logger.DEBUG)

        if result.result:
            result.output += logHelper(u"Failed Download Processing succeeded: (" + str(nzbName) + ", " + dirName + ")")
        else:
            result.output += logHelper(
                u"Failed Download Processing failed: (" + str(nzbName) + ", " + dirName + "): " + process_fail_message,
                logger.WARNING)
