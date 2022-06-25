'''
@author: mizaki
'''
#!/usr/bin/python
import sys
import os
from pathlib import Path
import re
import operator
import requests
import zipfile
from unrar.cffi import rarfile
import tempfile
import subprocess
import shutil

#Command line options
FileNextTo = False
RepackageRar = False
ManualSplit = False
DryRun = False
IgnoreArticles = False
SplitBy = ' '

#Globals
ComicFileType = ''
SearchForTitle = ''
FilenameSplit = ''
#Store for non-alpha i.e. Japanese.
AltSearchForTitle = ''
PossibleChapterTitleText = ''

def ziphasfile(zipname, filename):
    if filename is None: return None
    z = zipfile.ZipFile(zipname)
    if filename in z.namelist(): return True

def rarhasfile(rarname, filename):
    if filename is None: return None
    r = rarfile.RarFile(rarname)
    if filename in r.namelist(): return True

def which(program):
    #Returns path of the executable, if it exists

    def is_exe(fpath):
        return os.path.isfile(fpath) and os.access(fpath, os.X_OK)

    fpath, _ = os.path.split(program)
    if fpath:
        if is_exe(program):
            return program
    else:
        for path in os.environ["PATH"].split(os.pathsep):
            exe_file = os.path.join(path, program)
            if is_exe(exe_file):
                return exe_file

    return None

def isArticle(word):
    word = word.lower()
    articles = [
        "&",
        "a",
        "am",
        "an",
        "and",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "if",
        "is",
        "issue",
        "it",
        "it's",
        "its",
        "itself",
        "of",
        "or",
        "so",
        "the",
        "the",
        "with",
    ]
    if word in articles:
        return True

def matchconfidence(search, match):
    search = search.lower()
    match = match.lower()
    confidence = 0
    confidenceWordCount = 0
    confidenceWeightAdjust = 0
    if search == match:
        return 100
    else:
        searchWords = search.split()
        matchWords = match.split()
        lenDiff = len(searchWords) - len(matchWords)
        #lenDiff = abs(lenDiff) +1
        #Alter value based on difference between lengths
        if lenDiff:
            maxConfidencePerWord = 100 / len(matchWords)
            confidenceWeightAdjust = maxConfidencePerWord / lenDiff #len(searchWords)
        else:
            #Lengths match
            maxConfidencePerWord = 100 / len(searchWords)

        for i, sword in enumerate(searchWords):
            #if not isArticle(sword):
            for n, mword in enumerate(matchWords):
                if IgnoreArticles and isArticle(sword):
                    #print('Ignore: ' + sword)
                    break
                #Want to give a larger weight to words in the right order?
                #The Tail Before Time vs A Tail Before Time. Tail Before Time search would be equal.
                #The Tail Before Our Time vs The Tail Before Time Stopped. The Tail Before Time search should score The Tail Before Time Stopped higher as the word order matches better.
                #Search for: The Tail Before Time. Match for: The Tail Before Our Time. 25 25 25 0 ?
                #When reaching "Our" i=4 and n=5. maxConfidencePerWord / len(searchWords) [25 / 4 = 6.25]
                #To get adjusted weight: i - n and convert negative to positive then times that by adjust. e.g. 4-5=-1 means adjust is 6.25. Minus 6.25 from 25 confidence. 3-5=-2. 6.25*2=12.5. 25-12.5=12.5 confidence addition. Need to check not going below 0. Should use longest of the two search strings so can't go below 0?
                #TODO: Don't search for the same word more than once.
                #TODO: The Tail Before Time Past will equal 100 when it shouldn't.
                if sword == mword and i == n:
                    #print('perfect match: ' + sword + ' found: ' + mword)
                    confidence += maxConfidencePerWord
                elif sword == mword:
                    #print('imprefect match: i=' + str(i) + ' n=' + str(n) + ' Search: ' + sword + ' match: ' + mword)
                    #adjustor = confidenceWeightAdjust * abs((i-n))
                    adjustor = confidenceWeightAdjust + abs( (i+1)-(n+1) )
                    if adjustor < 0: adjustor = 0
                    #print('confidenceWeightAdjust: ' + str(confidenceWeightAdjust))
                    #print('adjustor: ' + str(adjustor))
                    confidence += maxConfidencePerWord - adjustor

    return round(confidence, 2)

def getSeriesInfo(series_id, hit_title, service):
    try:
        r = requests.get('https://api.mangaupdates.com/v1/series/' + series_id, timeout=10)
        if r.status_code == 200:
            res = r.json()
            res['hit_title'] = hit_title
            return res
        else:
            return None
    except Exception as err:
        print(f'Other error occurred: {err}')
        return None

def formatSeries(seriesInfo):
    #Always want a number for issue/number so default to 1.
    if MangaInfo['Number'] == '': MangaInfo['Number'] == '1'

    #Use hit_title rather than main title which is probably romanji
    if seriesInfo.get('hit_title'):
        MangaInfo['Series'] = seriesInfo.get('hit_title')
    else:
        if seriesInfo.get('title') is not None: MangaInfo['Series'] = seriesInfo.get('title')
    if seriesInfo.get('year') is not None: MangaInfo['Year'] = seriesInfo.get('year')
    if seriesInfo.get('bayesian_rating') is not None:
        #MU uses 0-10 and ComicInfo uses 0-5 so we'll devide by 2.
        rating = float(seriesInfo.get('bayesian_rating')) /2
        MangaInfo['CommunityRating'] = rating
    if seriesInfo.get('genres') is not None:
        genres = []
        for genreInfo in seriesInfo.get('genres'):
            genres.append(genreInfo['genre'])
        MangaInfo['Genre'] = ','.join(genres)
    if seriesInfo.get('categories') is not None:
        cats = []
        for catInfo in seriesInfo.get('categories'):
            cats.append(catInfo['category'])
        MangaInfo['Tags'] = ','.join(cats)
    if seriesInfo.get('authors') is not None:
        writers = []
        artists = []
        for artistInfo in seriesInfo.get('authors'):
            if artistInfo['type'] == 'Author': writers.append(artistInfo['name'])
            if artistInfo['type'] == 'Artist': artists.append(artistInfo['name'])
        MangaInfo['Writer'] = ','.join(writers)
        MangaInfo['Penciller'] = ','.join(artists)
    if seriesInfo.get('publishers') is not None:
        publishers = []
        for pubInfo in seriesInfo.get('publishers'):
            publishers.append(pubInfo['publisher_name'])
        MangaInfo['Publisher'] = ','.join(publishers)
    if seriesInfo.get('description') is not None:
        desc = seriesInfo.get('description')
        desc = re.sub('<br>', '\n', desc, flags=re.IGNORECASE)
        desc = re.sub('</p>', '\n\n', desc, flags=re.IGNORECASE)
        desc = re.sub('</li>', '\n', desc, flags=re.IGNORECASE)
        desc = re.sub('<h\d>', '*', desc, flags=re.IGNORECASE)
        desc = re.sub('</h\d>', '*\n', desc, flags=re.IGNORECASE)
        desc = re.sub('&nbsp;', ' ', desc, flags=re.IGNORECASE)
        desc = re.sub('&amp;', '&', desc, flags=re.IGNORECASE)
        desc = re.sub('&#039;', '\'', desc, flags=re.IGNORECASE)
        #Blanket replace any remaining <> or other &xxx;
        desc = re.sub('<|>', ' ', desc)
        desc = re.sub('&.{3,5};', ' ', desc)

        MangaInfo['Summary'] = desc
    if seriesInfo.get('url') is not None: MangaInfo['Web'] = seriesInfo.get('url')
    if seriesInfo.get('type') == 'Manga': MangaInfo['Manga'] = 'Yes'

def rebuildZip(original):
    #This recompresses the zip archive, without the files in the exclude_list
    try:
        with zipfile.ZipFile(
            tempfile.NamedTemporaryFile(dir=FilePath, delete=False), 'w', allowZip64=True) as newZip:
            with zipfile.ZipFile(original, mode='r') as zin:
                for item in zin.infolist():
                    buffer = zin.read(item.filename)
                    if item.filename != 'ComicInfo.xml':
                        newZip.writestr(item, buffer)
                newZip.writestr('ComicInfo.xml', outputXML)

                # preserve the old comment
                newZip.comment = zin.comment

            # replace with the new file
            fileDel = Path(FullFilenamePath)
            fileDel.unlink(missing_ok=True)
            newZip.close()  # Required on windows

            shutil.move(newZip.filename, FullFilenamePath)

    except (zipfile.BadZipfile, OSError) as e:
        print('Error rebuilding zip file: ' + e)
        return False
    return True

def rebuildRarToZip(original, delOriginal = True):
    #This recompresses the rar archive to a zip one, without comicinfo.xml
    try:
        with zipfile.ZipFile(
            tempfile.NamedTemporaryFile(dir=FilePath, delete=False), 'w', allowZip64=True) as newZip:
            rin = rarfile.RarFile(original)
            for item in rin.infolist():
                buffer = rin.read(item.filename)
                if item.filename != 'ComicInfo.xml' and item.file_size > 0:
                    newZip.writestr(item.filename, buffer)
            newZip.writestr('ComicInfo.xml', outputXML)

            # preserve the old comment
            newZip.comment = rin.comment

            # replace with the new file
            if delOriginal:
                fileDel = Path(FullFilenamePath)
                fileDel.unlink(missing_ok=True)
            newZip.close()  # Required on windows

            shutil.move(newZip.filename, FullFilenamePath + '.cbz')

    except (zipfile.BadZipfile, OSError) as e:
        print('Error rebuilding zip file: ' + e)
        return False
    return True

#Options a = ignore articles in name. o = output xml next to file. r = repackage rar to zip. s = string to split by character(s), single space as default.. e.g. -s=_
if len(sys.argv) <2:
    sys.exit('Filename missing! \nUsage: <options> <Filename>\nExiting...')

#Avalible externals programs
externalUnzip = which('unzip')
externalZip = which('zip')
externalRar = which('rar')
externalUnrar = which('unrar')

#https://anansi-project.github.io/docs/comicinfo/schemas/v2.1
MangaInfo = {'Title':'', 'Series':'', 'Number':'', 'Count':0, 'Volume':0, 'AlternateSeries':'', 'AlternateNumber':'', 'AlternateCount':0,'Summary':'', 'Notes':'', 'Year':0, 'Month':0, 'Day':0, 'Writer':'', 'Penciller':'', 'Inker':'', 'Colorist':'', 'Letterer':'', 'CoverArtist': '', 'Editor': '', 'Translator':'', 'Publisher':'', 'Imprint':'', 'Genre':'', 'Tags':'', 'Web':'', 'PageCount':0, 'LanguageISO':'', 'Format':'', 'BlackAndWhite': '', 'Manga': '', 'Characters':'', 'Teams':'', 'Locations':'', 'ScanInformation':'', 'StoryArc':'', 'StoryArcNumber':'', 'SeriesGroup':'', 'AgeRating':'', 'Pages': [], 'CommunityRating':0}

#Parse for Filename and options.

for n, opts in enumerate(sys.argv):
    #print(opts)
    #magic.from_file(opts) -- Using magic number is a little OTT.
    if n > 0:
        if opts.startswith('-'):
            if opts == '-o':
                #Output comicinfo.xml next to Filename
                FileNextTo = True
            if opts == '-r':
                #Repackage cbr to cbz
                RepackageRar = True
            if opts == '-d':
                DryRun = True
            if opts == '-a':
                IgnoreArticles = True
            if opts.startswith('-s'):
                #Split by character(s). If it's empty default is space.
                #print(opts.split('='))
                if opts.split('=')[1]:
                    SplitBy = opts.split('=')[1]
                    ManualSplit = True
        else:
            FilePath, FullFilename = os.path.split(opts)
            Filename, FilenameExt = os.path.splitext(FullFilename)
            FullFilenamePath = opts

if FilenameExt not in [ '.cbz',  '.cbr', '.zip', '.rar', '.cb7', '7z']:
    sys.exit('Expecting cbz, cbr, zip, rar, cb7 or 7z. \nExiting...')

#Test file is zip, z7, rar
if FilenameExt in ['.cbz','.zip']: ComicFileType = 'zip'
if FilenameExt in ['.cbr', '.rar']: ComicFileType = 'rar'
if FilenameExt in ['.cb7', '.7z']: ComicFileType = '7z'


#Parse Filename
AfterChapter = False
AfterHyphon = False
NotASCII = False
SkipWordFromSearch = False

#Split filename by spaces default then search for underscore.
if ManualSplit:
    FilenameSplit = Filename.split(SplitBy)
else:
    FilenameSplit = Filename.split()
    #Try to guess underscores are used for spaces.
    if not Filename.isalnum() and len(FilenameSplit) == 1:
        #Check for  some kind of delimter between words that is not a space.
        #print('look for demlimter')
        if len(Filename.split('_')) > 1:
            FilenameSplit = Filename.split('_')


if FilenameSplit:
    for i, ftext in enumerate(FilenameSplit):
        #print(i,ftext)
        #Flag a chapter delim found and treat number after as issue/chapter/number. E.g. naruto chapter 548 the big fight.cbz e.g. chapter 12, chaper12, chap 12, chap12, ch 12, ch12, ch. 12, ch.12, chap.12, chap. 12
        if ftext.lower().startswith('ch'):
            #print(ftext.lower())
            if ftext.lower() in ['chapter', 'chap', 'chap.', 'ch', 'ch.']:
                #print('found lone chapter')
                AfterChapter = True
                SkipWordFromSearch = True
            else:
                #print('found chapter')
                #Check for chapter1, ch.231, etc. Will also catch chatter123 but is very far edge case.
                numberforissue = ''
                #Walk the text for numbers
                for letter in ftext:
                    if letter.isnumeric(): numberforissue += letter
                #print('iss: ' + numberforissue)
                if numberforissue:
                    MangaInfo['Number'] = numberforissue
                    AfterChapter = True
                    SkipWordFromSearch = True

        if ftext.startswith('#'):
            if ftext[1:].isnumeric: MangaInfo['Number'] = ftext[1:]
        if ftext == '#':
            AfterChapter = True
            SkipWordFromSearch = True

        if ftext.isnumeric() and AfterChapter:
            #print('is num after chapter ident')
            #print(len(FilenameSplit))
            MangaInfo['Number'] = ftext
            #AfterChapter = False
        elif ftext.isnumeric():
            #Number on its own and number is currently empty.
            if not MangaInfo['Number']:
                MangaInfo['Number'] = ftext
                SkipWordFromSearch = True
                
        #Remove start tags [Group]Title
        if i == 0 and ftext.startswith('['):
            #print('has tag')
            endBracket = ftext.find(']')
            if endBracket > 0:
                ftext = ftext[endBracket +1:]

        #Non-roman characters
        if not ftext.isascii():
            NotASCII = True
            SkipWordFromSearch = True
            AltSearchForTitle += ' ' + ftext
            #print('not ascii: ' + str(NotASCII))
        else:
            NotASCII = False

        #Strip hyphon. Naruto chapter 353 - The fight for noodles.
        if ftext.startswith('-'):
            #Lone hyphon?
            if len(ftext) == 1:
                #ftext = ''
                AfterHyphon = True
                SkipWordFromSearch = True
            #Could be a title after
            if not ftext[1:] in ['san', 'chan', 'sama', 'dono', 'kun']:
                AfterHyphon = True
                SkipWordFromSearch = True

        if AfterHyphon:
            if ftext.startswith('-'):
                PossibleChapterTitleText += ' ' + ftext[1:]
            else:
                PossibleChapterTitleText += ' ' + ftext

        #Last chars are numbers assume issue number.
        #if i == len(FilenameSplit): MangaInfo['Number'] = ftext
        if i == len(FilenameSplit)-1:
            #Check last split is like 1u.
            #print('last')
            if ftext.isnumeric() and MangaInfo['Number'] == '':
                #Last chars are numbers, assume issue
                MangaInfo['Number'] = ftext
                SkipWordFromSearch = True
            if ftext[:-1].isnumeric() and MangaInfo['Number'] == '':
                #Last chars are numbers, assume issue
                #print('last is digit')
                MangaInfo['Number'] = ftext[:-1]
                SkipWordFromSearch = True

            #Check last split is like 2.5. Should assume 2 would be vol and 5 issue? That doesn't tie with .5 being a colour etc.
            if ftext in ['.']:
                ftextSplit = ftext.split()
                if ftextSplit[0].isnumeric(): MangaInfo['Number'] = ftextSplit[0]
                SkipWordFromSearch = True

            #Parse out possible number. e.g. naruto263
            if MangaInfo['Number'] == '':
                #print('Check for naruto263 type')
                renum = re.search('([A-Za-z]+)(\d+)', ftext)
                if renum is not None: MangaInfo['Number'] = renum.group(2)
                #Alter text to remove number from search.
                ftext = renum.group(1)
        #Build search title
        #if (not AfterChapter or not NotASCII) or (not AfterChapter and not NotASCII):
        if not SkipWordFromSearch and ftext:
            #print('add to search')
            #if not AfterChapter: print('not AfterChapter')
            SearchForTitle += ' ' + ftext

#Enter a suspected title. Can overwrite later if data comes in.
if PossibleChapterTitleText: MangaInfo['Title'] = PossibleChapterTitleText

#Strip [ ] tags.
#searchForTags = re.search('\[.*\](.*)', SearchForTitle)
#if searchForTags is not None: SearchForTitle = searchForTags.group(1)

#API
try:
    rs = requests.post('https://api.mangaupdates.com/v1/series/search', data={'search': SearchForTitle.strip()})
    SearchResult = rs.json()
except Exception as e:
    sys.exit('Failed to connect: ' + e)
    

#Instead of trying to order the json, create a lookup table.
lookupTable = []

def buildMenu(confidenceLevel, op):
    for k, v in enumerate(SearchResult['results']):
        if  op(SearchResult['results'][k]['record']['confidence'], confidenceLevel):
            lookupTable.append(k)
            #print(str(k) + ' :  ' + str(SearchResult['results'][k]['record']['title']) + '[' + str(SearchResult['results'][k]['record']['confidence']) + '%]')
            print(str(len(lookupTable)) + ' :  ' + str(SearchResult['results'][k]['hit_title']) + '[' + str(SearchResult['results'][k]['record']['confidence']) + '%]')
            #print(str(orderListNum) + ' :  ' + str(SearchResult['results'][k]['hit_title']) + '[' + str(SearchResult['results'][k]['record']['confidence']) + '%]')
    print('e: Show low confidence titles')
    print('m: Enter search title manually')
    print('q: Quit')
    print('Searched for: ' + SearchForTitle.strip())

for k, v in enumerate(SearchResult['results']):
    #Confidence value: words as a percentage of matches. Maybe then weight number of letters in word?
    confidence = matchconfidence(SearchForTitle.strip(), str(SearchResult['results'][k]['hit_title']))
    #print(str(k) + ' :  ' + str(SearchResult['results'][k]['record']['title']) + '[' + str(confidence) + '%]')
    SearchResult['results'][k]['record']['confidence'] = confidence

buildMenu(50, operator.gt)

def processChoice(choice):
    if choice.isnumeric():
        clookup = None
        seriesID = None
        try:
            clookup = lookupTable[int(choice)-1]
        except:
            print('Bad ID number.')
            inputChoice()
        try:
            seriesID = str(SearchResult['results'][clookup]['record']['series_id'])
        except:
            print('Failed to get series info.')
            buildMenu(50, operator.gt)

        if seriesID:
            seriesInfo = getSeriesInfo(seriesID, SearchResult['results'][clookup]['hit_title'], 'MU')
            #seriesInfo = requests.get('https://api.mangaupdates.com/v1/series/' + str(SearchResult['results'][clookup]['record']['series_id']))
            if seriesInfo: formatSeries(seriesInfo)
        else:
            print('Failed to get series info.')
            buildMenu(50, operator.gt)
    else:
        if choice == 'q': sys.exit('Exiting. Nothing written')
        elif choice == 'e':
            print('Low confidence titles:')
            buildMenu(50, operator.lt)
            inputChoice()
        elif choice == 'm':
            #TODO Manual entry
            pass
            
def inputChoice():
    choice = input('Enter number: ')
    processChoice(choice)

inputChoice()

#Generate XML
outputXML = '<?xml version=\'1.0\' encoding=\'utf-8\'?>\n'
outputXML += '<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:xsd="http://www.w3.org/2001/XMLSchema">\n'
for item in MangaInfo:
    if MangaInfo[item]:
        #print(MangaInfo[item])
        outputXML += '  <' + item + '>' + str(MangaInfo[item]) + '</' + item + '>\n'
outputXML += '</ComicInfo>'
#print(outputXML)

#Output xml file next to comic file with <filename>.xml
if FileNextTo and not DryRun:
    with open(Filename + '.xml', 'w', encoding='utf-8') as f:
        for line in outputXML:
            f.write(line)
elif not FileNextTo and not DryRun:
    if ComicFileType == 'zip':
        #Write to zip file.
        #Check for ComicInfo.xml already in zip file.
        if not ziphasfile(FullFilenamePath, 'ComicInfo.xml'):
            with zipfile.ZipFile(FullFilenamePath, 'a') as zipped_f:
                zipped_f.writestr('ComicInfo.xml', outputXML)
        else:
            print('ComicInfo.xml already exists!')
            with zipfile.ZipFile(FullFilenamePath, 'r') as zipped_f:
                print('Current ComicInfo.xml:')
                print(zipped_f.read('ComicInfo.xml'))
            choiceOverwrite = input('Overwrite? Y/N: ')
            if choiceOverwrite.lower() == 'y':
                print('Overwriting file...')
                if externalZip:
                    try:
                        subprocess.run(['zip', FullFilenamePath, '-d', 'ComicInfo.xml'])
                        if not ziphasfile(FullFilenamePath, 'ComicInfo.xml'):
                            with zipfile.ZipFile(FullFilenamePath, 'a') as zipped_f:
                                zipped_f.writestr('ComicInfo.xml', outputXML)
                                print('Wrote new ComicInfo.xml')
                    except Exception as e:
                        print('Failed to write new ComicInfo.xml')
                        print(e)
                        print('Rebuilding zip file...')
                        if rebuildZip(FullFilenamePath):
                            print('New ComicInfo.xml written.')
                        else:
                            sys.exit('Failed to create new zip file. Exiting...')
                else:
                    if rebuildZip(FullFilenamePath):
                        print('New ComicInfo.xml written.')
            else:
                sys.exit('Leaving ComicInfo.xml untouched. Exiting...')
    elif ComicFileType == 'rar':
        #Check for external rar tool
        if externalRar:
            #Write to rar file (if we can)
            #Check for comicinfo.xml in rar
            if not rarhasfile(FullFilenamePath, 'ComicInfo.xml'):
                #Is rar available?
                if externalRar:
                    try:
                        #Create a tempory file to add to rar archive
                        #TODO file permission on ComicInfo.xml
                        subprocess.run(['rar', 'a', FullFilenamePath, '-ep', '-siComicInfo.xml'], input=bytes(outputXML, 'UTF-8'))
                    except Exception as e:
                        print(e)
                
            else:
                print('Rar has ComicInfo.xml file.')
                #with rarfile.RarFile(FullFilenamePath) as rin:
                rin = rarfile.RarFile(FullFilenamePath)
                #with rarfile.RarFile(FullFilenamePath) as rar_f:
                print('Current ComicInfo.xml:')
                print(rin.read('ComicInfo.xml'))
                choiceOverwrite = input('Overwrite? Y/N: ')
                if choiceOverwrite.lower() == 'y':
                    print('Overwriting file...')
                #Use rar to update the file
                    try:
                        subprocess.run(['rar', 'u', FullFilenamePath, '-ep', '-siComicInfo.xml'], input=bytes(outputXML, 'UTF-8'))
                    except Exception as e:
                        print(e)
                else:
                    sys.exit('Leaving ComicInfo.xml untouched. Exiting...')
        else:
            #No external rar tool, offer to create zip instead
            print('No external rar program.')
            if RepackageRar:
                if rebuildRarToZip(FullFilenamePath):
                    print('Converted rar to zip.')
                else:
                    print('Failed to create zip file from rar.')
            else:
                choiceOverwrite = input('Create CBZ? Y/N: ')
                if choiceOverwrite.lower() == 'y':
                    #Create zip from rar
                    if rebuildRarToZip(FullFilenamePath):
                        print('Converted rar to zip.')
                    else:
                        print('Failed to create zip file from rar.')

elif DryRun:
    print('Dry run. Would have written:')
    print(outputXML)
