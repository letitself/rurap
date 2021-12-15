from flask import Flask, render_template, url_for, request, redirect

import lyricsgenius
import re
import time
import sqlite3
import nltk
import logging as lg
#nltk.download("punkt")

import pymorphy2
m = pymorphy2.MorphAnalyzer()

app = Flask(__name__)


@app.route('/')
@app.route('/index')
def index():
    return render_template('index.html')


@app.route('/instructions')
def instructions():
    return render_template('instructions.html')


genius = lyricsgenius.Genius("Oxrp7vCqqro2ISU1Ciy8083kkt8jFN_ev6colQxvYJ_Vyfl2a7EShHSLbC95nRnC", skip_non_songs=True, excluded_terms=["(Remix)", "(Live)"], remove_section_headers=True)
database_path = "data.db"

albums_list = []


def clean(lyric):
    return re.sub("\u2005|\u205f|\xa0", " ", lyric)


def get_lyrics(track):
    lyrics = genius.lyrics(track["id"])
    if lyrics:
        if lyrics.endswith("EmbedShare URLCopyEmbedCopy"):
            lyrics = re.sub("\d*EmbedShare URLCopyEmbedCopy$", "", lyrics)
    return lyrics


def get_tracks(album):
    tracks = []
    album_info = genius.search_album(album[0], album[1])
    if album_info:
        for track in album_info.tracks:
            tracks.append({
                "title": track._body["song"]["title"],
                "artist": track._body["song"]["primary_artist"]["name"],
                "id": track.id,
                "album": album_info._body["name"],
                "year": album_info._body["release_date_components"]["year"]
            })
            tracks[-1]["lyrics"] = get_lyrics(tracks[-1])
    return tracks


def get_albums(albums_list):
    connection = sqlite3.connect(database_path)
    cur = connection.cursor()
    
    cur.execute(f"DROP TABLE IF EXISTS Tracks")
    cur.execute(
        """CREATE TABLE "Tracks" ("id" TEXT UNIQUE NOT NULL, "title" TEXT, "artist" TEXT, "album" TEXT, "year" INTEGER, "lyrics" TEXT, PRIMARY KEY("id"))""")
    for album in albums_list:
        tracks = get_tracks(album)
        for track in tracks:
            try:
                cur.execute("INSERT INTO Tracks VALUES (?, ?, ?, ?, ?, ?)", (
                track["id"], track["title"], track["artist"], track["album"], track["year"], track["lyrics"]))
                connection.commit()
            except sqlite3.IntegrityError:
                pass


def split_to_strings():
    connection = sqlite3.connect(database_path)
    cur = connection.cursor()
    
    cur.execute(f"DROP TABLE IF EXISTS Lyrics")
    cur.execute(
        """CREATE TABLE "Lyrics" ("lyric_id" INTEGER UNIQUE NOT NULL, "intrack_id" INTEGER NOT NULL, "track_id" TEXT NOT NULL, "lyric" TEXT NOT NULL, PRIMARY KEY("lyric_id"))""")
    tracks = cur.execute("SELECT * FROM Tracks").fetchall()

    for track in tracks:
        try:
            text = re.split("\n", track[5])  # text = [lyric1, lyric2, …]
        except TypeError:
            if isinstance(text, list):
                pass

        for i in range(len(text)):
            if len(text[i]) > 0 and re.search("[А-Яа-яЁёA-Za-z\d]", text[i]) is not None:
                lyric_id = cur.execute("SELECT COUNT(*) FROM Lyrics").fetchone()[0]
                cur.execute("INSERT INTO Lyrics VALUES (?, ?, ?, ?)", (lyric_id, i, track[0], text[i]))

    connection.commit()


def tokenize():
    connection = sqlite3.connect(database_path)
    cur = connection.cursor()
    
    tokendict = {}

    cur.execute(f"DROP TABLE IF EXISTS Tokens")
    cur.execute(
        """CREATE TABLE "Tokens" ("token_id" INTEGER UNIQUE NOT NULL, "inlyric_id" INTEGER NOT NULL, "lyric_id" INTEGER NOT NULL, "token" TEXT NOT NULL, "lemma" TEXT NOT NULL, "pos" TEXT, "tags" TEXT, PRIMARY KEY("token_id"))""")

    lyrics = cur.execute("SELECT * FROM Lyrics").fetchall()

    n = len(lyrics)

    for i in range(len(lyrics)):
        tokens = nltk.word_tokenize(lyrics[i][3])
        if i % 100 == 0:
            print(f"{i} / {n} lyrics")

        counter = 0

        for token in tokens:
            if token in tokendict:
                pos_tag = tokendict[token]["pos_tag"]
                other_tags = tokendict[token]["other_tags"]
                lemma = tokendict[token]["lemma"]

            else:
                results = m.parse(token)[0]

                if "PNCT" in results.tag:
                    break

                tagline = results.tag.cyr2lat(results.tag)
                pos_index = re.match("[A-Z]+", tagline).end()
                pos_tag = tagline[:pos_index]
                other_tags = re.sub(",", " ", tagline[pos_index:]).strip(" ")
                lemma = results.normal_form

                tokendict[token] = {
                    "pos_tag": pos_tag,
                    "other_tags": other_tags,
                    "lemma": lemma
                }

            token_id = cur.execute("SELECT COUNT(*) FROM Tokens").fetchone()[0]
            cur.execute("INSERT INTO Tokens VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (token_id, counter, lyrics[i][0], token, lemma, pos_tag, other_tags))

            counter += 1

        connection.commit()


def is_literal_token(property):
    quotation = """«»"„“'"""
    if property[0] in quotation and property[-1] in quotation:
        return True
    return False


def unquote(token):
    return token.strip("""«»"„“'\"""")


def is_pos(property):
    if re.search("[^A-Z]", property) is None:
        return True
    return False


def analyze_query_token(token):
    connection = sqlite3.connect(database_path)
    cur = connection.cursor()
    
    literal_token = None
    lemma = None
    pos = None
    
    for property in token:
        if is_literal_token(property):
            if not literal_token:
                literal_token = unquote(property)
            else:
                lg.error(f"More than one tokens are found in your query.")
                break
        elif is_pos(property):
            if not pos:
                pos = property
            else:
                lg.error(f"More than one POS are found in your query.")
                break
        else:
            if not lemma:
                lemma = m.parse(property)[0].normal_form
            else:
                lg.error(f"More than one lemmas are found in your query.")
                break
    
    return literal_token, lemma, pos


def find_token(token):
    connection = sqlite3.connect(database_path)
    cur = connection.cursor()
    
    literal_token, lemma, pos = analyze_query_token(token)
    
    query = "SELECT token, token_id, inlyric_id, lyric, Lyrics.lyric_id as lyric_id, title, track_id, album, artist, year FROM Tokens JOIN Lyrics ON Tokens.lyric_id = Lyrics.lyric_id JOIN Tracks ON Lyrics.track_id = Tracks.id WHERE "
    
    literal_token_query = "token = ? AND " if literal_token else ""
    lemma_query = "lemma = ? AND " if lemma else ""
    pos_query = "pos = ? AND " if pos else ""
    
    query = f"{query}{literal_token_query}{lemma_query}{pos_query}"
    query = re.sub("AND $", "", query)

    if "token = " in query:
        if "lemma = " in query:
            if "pos = " in query:
                results = cur.execute(query, (literal_token, lemma, pos)).fetchall()
            else:
                results = cur.execute(query, (literal_token, lemma)).fetchall()
        else:
            if "pos = " in query:
                results = cur.execute(query, (literal_token, pos)).fetchall()
            else:
                results = cur.execute(query, (literal_token,)).fetchall()
    else:
        if "lemma = " in query:
            if "pos = " in query:
                results = cur.execute(query, (lemma, pos)).fetchall()
            else:
                results = cur.execute(query, (lemma,)).fetchall()
        else:
            if "pos = " in query:
                results = cur.execute(query, (pos,)).fetchall()
            else:
                lg.error("No lemmas, no tokens, no POS. What have you typed here?")
                return []
    
    for i in range(len(results)):
        results[i] = {
            "token": results[i][0],
            "token_id": results[i][1],
            "inlyric_id": results[i][2],
            "lyric": clean(results[i][3]),
            "lyric_id": results[i][4],
            "title": clean(results[i][5]),
            "track_id": results[i][6],
            "album": clean(results[i][7]),
            "artist": clean(results[i][8]),
            "year": results[i][9]
        }
    
    return results


def search_query(query):
    connection = sqlite3.connect(database_path)
    cur = connection.cursor()
    
    query = re.split("[ \t]+", query.strip(" "))
    
    results = []
    
    for i in range(len(query)):
        
        token = re.split("\+", query[i].strip())
        results.append(find_token(token))
    
    chains = []
    for a in range(len(results[0])):
        chain = [results[0][a]]
        
        for k in range(1, len(results)):
            if chain:
                
                found = False
                
                for b in range(len(results[k])):
                    
                    if results[0][a]["lyric_id"] == results[k][b]["lyric_id"]:
                        if results[0][a]["inlyric_id"] + k == results[k][b]["inlyric_id"]:
                            found = True
                            break
                
                if found:
                    chain.append(results[k][b])
                else:
                    chain = None
        
        if chain:
            chains.append(chain)
            print(chain)
    return chains


@app.route('/track/<track_id>', methods=['GET', 'POST'])
def track(track_id):
    if track_id == "search":
        return render_template("index.html")
    
    connection = sqlite3.connect(database_path)
    cur = connection.cursor()
    
    track = cur.execute("SELECT lyrics, title, id, album, artist, year FROM Tracks WHERE id = ?", (track_id,)).fetchall()[0]
    
    track = {
        "title": track[1],
        "artist": track[4],
        "album": track[3],
        "year": track[5],
        "lyrics": track[0]
    }
    
    return render_template("track.html", track=track)


@app.route('/search', methods=['GET', 'POST'])
def search(query=None):
    if request.method == 'POST':
        query = request.form['query']
        if re.search("[А-Яа-яЁёA-Za-z\d]+", query) is None:
            return render_template("none.html", query=query)
        
        results = search_query(query)
        if results == []:
            return render_template("none.html", query=query)
        else:
            return render_template("results.html", results=results, query=query)


if __name__ == '__main__':
    app.run(debug = True)






