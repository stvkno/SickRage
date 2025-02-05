# Author: Nic Wolfe <nic@wolfeden.ca>
# URL: https://sickrage.github.io
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
#  GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with SickRage.  If not, see <http://www.gnu.org/licenses/>.


import requests
import re

try:
    import xml.etree.cElementTree as etree
except ImportError:
    import xml.etree.ElementTree as etree

from sickbeard import logger, classes, helpers
from sickbeard.common import Quality
from sickrage.helper.encoding import ek, ss
from sickrage.helper.exceptions import ex

from name_parser.parser import NameParser, InvalidNameException, InvalidShowException


def getSeasonNZBs(name, urlData, season):
    """
    Split a season NZB into episodes

    :param name: NZB name
    :param urlData: URL to get data from
    :param season: Season to check
    :return: dict of (episode files, xml matches)
    """
    try:
        showXML = etree.ElementTree(etree.XML(urlData))
    except SyntaxError:
        logger.log(u"Unable to parse the XML of " + name + ", not splitting it", logger.ERROR)
        return ({}, '')

    filename = name.replace(".nzb", "")

    nzbElement = showXML.getroot()

    regex = '([\w\._\ ]+)[\. ]S%02d[\. ]([\w\._\-\ ]+)[\- ]([\w_\-\ ]+?)' % season

    sceneNameMatch = re.search(regex, filename, re.I)
    if sceneNameMatch:
        showName, qualitySection, groupName = sceneNameMatch.groups()  # @UnusedVariable
    else:
        logger.log(u"Unable to parse " + name + " into a scene name. If it's a valid one log a bug.", logger.ERROR)
        return ({}, '')

    regex = '(' + re.escape(showName) + '\.S%02d(?:[E0-9]+)\.[\w\._]+\-\w+' % season + ')'
    regex = regex.replace(' ', '.')

    epFiles = {}
    xmlns = None

    for curFile in nzbElement.getchildren():
        xmlnsMatch = re.match("\{(http:\/\/[A-Za-z0-9_\.\/]+\/nzb)\}file", curFile.tag)
        if not xmlnsMatch:
            continue
        else:
            xmlns = xmlnsMatch.group(1)
        match = re.search(regex, curFile.get("subject"), re.I)
        if not match:
            # print curFile.get("subject"), "doesn't match", regex
            continue
        curEp = match.group(1)
        if curEp not in epFiles:
            epFiles[curEp] = [curFile]
        else:
            epFiles[curEp].append(curFile)

    return (epFiles, xmlns)


def createNZBString(fileElements, xmlns):
    rootElement = etree.Element("nzb")
    if xmlns:
        rootElement.set("xmlns", xmlns)

    for curFile in fileElements:
        rootElement.append(stripNS(curFile, xmlns))

    return etree.tostring(ss(rootElement))


def saveNZB(nzbName, nzbString):
    """
    Save NZB to disk

    :param nzbName: Filename/path to write to
    :param nzbString: Content to write in file
    """
    try:
        with ek(open, nzbName + ".nzb", 'w') as nzb_fh:
            nzb_fh.write(nzbString)

    except EnvironmentError, e:
        logger.log(u"Unable to save NZB: " + ex(e), logger.ERROR)


def stripNS(element, ns):
    element.tag = element.tag.replace("{" + ns + "}", "")
    for curChild in element.getchildren():
        stripNS(curChild, ns)

    return element


def splitResult(result):
    """
    Split result into seperate episodes

    :param result: search result object
    :return: False upon failure, a list of episode objects otherwise
    """
    urlData = helpers.getURL(result.url, session=requests.Session(), needBytes=True)
    if urlData is None:
        logger.log(u"Unable to load url " + result.url + ", can't download season NZB", logger.ERROR)
        return False

    # parse the season ep name
    try:
        np = NameParser(False, showObj=result.show)
        parse_result = np.parse(result.name)
    except InvalidNameException:
        logger.log(u"Unable to parse the filename " + result.name + " into a valid episode", logger.DEBUG)
        return False
    except InvalidShowException:
        logger.log(u"Unable to parse the filename " + result.name + " into a valid show", logger.DEBUG)
        return False

    # bust it up
    season = parse_result.season_number if parse_result.season_number != None else 1

    separateNZBs, xmlns = getSeasonNZBs(result.name, urlData, season)

    resultList = []

    for newNZB in separateNZBs:

        logger.log(u"Split out " + newNZB + " from " + result.name, logger.DEBUG)

        # parse the name
        try:
            np = NameParser(False, showObj=result.show)
            parse_result = np.parse(newNZB)
        except InvalidNameException:
            logger.log(u"Unable to parse the filename " + newNZB + " into a valid episode", logger.DEBUG)
            return False
        except InvalidShowException:
            logger.log(u"Unable to parse the filename " + newNZB + " into a valid show", logger.DEBUG)
            return False

        # make sure the result is sane
        if (parse_result.season_number != None and parse_result.season_number != season) or (
                parse_result.season_number == None and season != 1):
            logger.log(
                u"Found " + newNZB + " inside " + result.name + " but it doesn't seem to belong to the same season, ignoring it",
                logger.WARNING)
            continue
        elif len(parse_result.episode_numbers) == 0:
            logger.log(
                u"Found " + newNZB + " inside " + result.name + " but it doesn't seem to be a valid episode NZB, ignoring it",
                logger.WARNING)
            continue

        wantEp = True
        for epNo in parse_result.episode_numbers:
            if not result.extraInfo[0].wantEpisode(season, epNo, result.quality):
                logger.log(u"Ignoring result " + newNZB + " because we don't want an episode that is " +
                           Quality.qualityStrings[result.quality], logger.INFO)
                wantEp = False
                break
        if not wantEp:
            continue

        # get all the associated episode objects
        epObjList = []
        for curEp in parse_result.episode_numbers:
            epObjList.append(result.extraInfo[0].getEpisode(season, curEp))

        # make a result
        curResult = classes.NZBDataSearchResult(epObjList)
        curResult.name = newNZB
        curResult.provider = result.provider
        curResult.quality = result.quality
        curResult.extraInfo = [createNZBString(separateNZBs[newNZB], xmlns)]

        resultList.append(curResult)

    return resultList
