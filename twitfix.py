from flask import Flask, render_template, request, redirect, Response
import youtube_dl
import textwrap
import twitter
import pymongo
import json
import re
import os
import urllib.parse

app = Flask(__name__)
pathregex = re.compile("\\w{1,15}\\/status\\/\\d{2,20}")
generate_embed_user_agents = ["Mozilla/5.0 (Macintosh; Intel Mac OS X 10.10; rv:38.0) Gecko/20100101 Firefox/38.0", "Mozilla/5.0 (compatible; Discordbot/2.0; +https://discordapp.com)", "TelegramBot (like TwitterBot)", "Mozilla/5.0 (compatible; January/1.0; +https://gitlab.insrt.uk/revolt/january)", "test"]

# Read config from config.json. If it does not exist, create new.
if not os.path.exists("config.json"):
    with open("config.json", "w") as outfile:
        default_config = {"config":{"link_cache":"json","database":"[url to mongo database goes here]","method":"youtube-dl", "color":"#43B581", "appname": "TwitFix", "repo": "https://github.com/robinuniverse/twitfix", "url": "https://fxtwitter.com"},"api":{"api_key":"[api_key goes here]","api_secret":"[api_secret goes here]","access_token":"[access_token goes here]","access_secret":"[access_secret goes here]"}}
        json.dump(default_config, outfile, indent=4, sort_keys=True)

    config = default_config
else:
    f = open("config.json")
    config = json.load(f)
    f.close()

# If method is set to API or Hybrid, attempt to auth with the Twitter API
if config['config']['method'] in ('api', 'hybrid'):
    auth = twitter.oauth.OAuth(config['api']['access_token'], config['api']['access_secret'], config['api']['api_key'], config['api']['api_secret'])
    twitter_api = twitter.Twitter(auth=auth)

link_cache_system = config['config']['link_cache']

if link_cache_system == "json":
    link_cache = {}
    if not os.path.exists("config.json"):
        with open("config.json", "w") as outfile:
            default_link_cache = {"test":"test"}
            json.dump(default_link_cache, outfile, indent=4, sort_keys=True)

    f = open('links.json',)
    link_cache = json.load(f)
    f.close()
elif link_cache_system == "db":
    client = pymongo.MongoClient(config['config']['database'], connect=False)
    db = client.TwitFix

@app.route('/') # If the useragent is discord, return the embed, if not, redirect to configured repo directly
def default():
    user_agent = request.headers.get('user-agent')
    if user_agent in generate_embed_user_agents:
        return message("TwitFix is an attempt to fix twitter video embeds in discord! created by Robin Universe :)\n\n💖\n\nClick me to be redirected to the repo!")
    else:
        return redirect(config['config']['repo'], 301)

@app.route('/oembed.json')
def oembedend():
    desc = request.args.get("desc", None)
    user = request.args.get("user", None)
    link = request.args.get("link", None)
    return o_embed_gen(desc,user,link)

@app.route('/<path:sub_path>')
def twitfix(sub_path):
    user_agent = request.headers.get('user-agent')
    match = pathregex.search(sub_path)
    if match is not None:
        twitter_url = sub_path

        if match.start() == 0:
            twitter_url = "https://twitter.com/" + sub_path

        if user_agent in generate_embed_user_agents:
            res = embed_video(twitter_url)
            return res

        else:
            print("Redirect to " + twitter_url)
            return redirect(twitter_url, 301)
    else:
        return message("This doesn't appear to be a twitter URL")

@app.route('/other/<path:sub_path>') # Show all info that Youtube-DL can get about a video as a json
def other(sub_path):
    res = embed_video(sub_path)
    return res

@app.route('/info/<path:sub_path>') # Show all info that Youtube-DL can get about a video as a json
def info(sub_path):
    with youtube_dl.YoutubeDL({'outtmpl': '%(id)s.%(ext)s'}) as ydl:
        result = ydl.extract_info(sub_path, download=False)

    return result

@app.route('/dir/<path:sub_path>')
def dir(sub_path):
    user_agent = request.headers.get('user-agent')
    url = sub_path
    match = pathregex.search(url)
    if match is not None:
        twitter_url = url

        if match.start() == 0:
            twitter_url = "https://twitter.com/" + url

        if user_agent in generate_embed_user_agents:
            res = message('Click the link to be redirected to the Direct MP4 Link')
            return res

        else:
            print("Redirect to direct MP4 URL")
            return direct_video(twitter_url)
    else:
        return redirect(url, 301)

def direct_video(video_link): # Just get a redirect to a MP4 link from any tweet link
    cached_vnf = get_vnf_from_link_cache(video_link)
    if cached_vnf == None:
        try:
            vnf = link_to_vnf(video_link)
            add_vnf_to_link_cache(video_link, vnf)
            return redirect(vnf['url'], 301)
            print("Redirecting to direct URL: " + vnf['url'])
        except Exception as e:
            print(e)
            return message("Failed to scan your link!")
    else:
        return redirect(cached_vnf['url'], 301)
        print("Redirecting to direct URL: " + vnf['url'])

def embed_video(video_link): # Return Embed from any tweet link
    cached_vnf = get_vnf_from_link_cache(video_link)

    if cached_vnf == None:
        try:
            vnf = link_to_vnf(video_link)
            add_vnf_to_link_cache(video_link, vnf)
            return embed(video_link, vnf)

        except Exception as e:
            print(e)
            return message("Failed to scan your link!")
    else:
        return embed(video_link, cached_vnf)

def video_info(url, tweet="", desc="", thumb="", uploader=""): # Return a dict of video info with default values
    vnf = {
        "tweet"         :tweet,
        "url"           :url,
        "description"   :desc,
        "thumbnail"     :thumb,
        "uploader"      :uploader
    }
    return vnf

def link_to_vnf_from_api(video_link):
    print("Attempting to download tweet info from Twitter API")
    twid = int(re.sub(r'\?.*$','',video_link.rsplit("/", 1)[-1])) # gets the tweet ID as a int from the passed url
    tweet = twitter_api.statuses.show(_id=twid, tweet_mode="extended")

    # Check to see if tweet has a video, if not, make the url passed to the VNF the first t.co link in the tweet
    if 'extended_entities' in tweet:
        if 'video_info' in tweet['extended_entities']['media'][0]:
            if tweet['extended_entities']['media'][0]['video_info']['variants']:
                best_bitrate = 0
                thumb = tweet['extended_entities']['media'][0]['media_url']
                for video in tweet['extended_entities']['media'][0]['video_info']['variants']:
                    if video.content_type === "video/mp4" and video.bitrate > best_bitrate:
                        url = video.url
        else:
            url = re.findall(r'(https?://[^\s]+)', tweet['full_text'])[0]
            thumb = "Non video link with url"
            print("Non video tweet, but has a link: " + url)
    else:
        url = re.findall(r'(https?://[^\s]+)', tweet['full_text'])[0]
        thumb = "Non video link with url"
        print("Non video tweet, but has a link: " + url)

    if len(tweet['full_text']) > 200:
        text = textwrap.shorten(tweet['full_text'], width=200, placeholder="...")
    else:
        text = tweet['full_text']

    vnf = video_info(url, video_link, text, thumb, tweet['user']['name'])
    return vnf

def link_to_vnf_from_youtubedl(video_link):
    print("Attempting to download tweet info via YoutubeDL")
    with youtube_dl.YoutubeDL({'outtmpl': '%(id)s.%(ext)s'}) as ydl:
        result = ydl.extract_info(video_link, download=False)
        vnf = video_info(result['url'], video_link, result['description'].rsplit(' ',1)[0], result['thumbnail'], result['uploader'])
        return vnf

def link_to_vnf(video_link): # Return a VideoInfo object or die trying
    if config['config']['method'] == 'hybrid':
        try:
            return link_to_vnf_from_api(video_link)
        except Exception as e:
            print("API Failed")
            print(e)
            return link_to_vnf_from_youtubedl(video_link)
    elif config['config']['method'] == 'api':
        try:
            return link_to_vnf_from_api(video_link)
        except Exception as e:
            print("API Failed")
            print(e)
            return None
    elif config['config']['method'] == 'youtube-dl':
        try:
            return link_to_vnf_from_youtubedl(video_link)
        except Exception as e:
            print("Youtube-DL Failed")
            print(e)
            return None
    else:
        print("Please set the method key in your config file to 'api' 'youtube-dl' or 'hybrid'")
        return None

def get_vnf_from_link_cache(video_link):
    if link_cache_system == "db":
        collection = db.linkCache
        vnf = collection.find_one({'tweet': video_link})
        if vnf != None: 
            print("Link located in DB cache")
            return vnf
        else:
            print("Link not in DB cache")
            return None
    elif link_cache_system == "json":
        if video_link in link_cache:
            print("Link located in json cache")
            vnf = link_cache[video_link]
            return vnf
        else:
            print("Link not in json cache")
            return None

def add_vnf_to_link_cache(video_link, vnf):
    if link_cache_system == "db":
        try:
            out = db.linkCache.insert_one(vnf)
            print("Link added to DB cache")
            return True
        except Exception:
            print("Failed to add link to DB cache")
            return None
    elif link_cache_system == "json":
        link_cache[video_link] = vnf
        with open("links.json", "w") as outfile: 
            json.dump(link_cache, outfile, indent=4, sort_keys=True)
            return None

def message(text):
    return render_template('default.html', message=text, color=config['config']['color'], appname=config['config']['appname'], repo=config['config']['repo'], url=config['config']['url'])

def embed(video_link, vnf):
    print(vnf['url'])
    if vnf['url'].startswith('https://t.co') is not True:
        desc = re.sub(r' http.*t\.co\S+', '', vnf['description'])
        urlUser = urllib.parse.quote(vnf['uploader'])
        urlDesc = urllib.parse.quote(desc)
        urlLink = urllib.parse.quote(video_link)
        return render_template('index.html', vidurl=vnf['url'], desc=desc, pic=vnf['thumbnail'], user=vnf['uploader'], video_link=video_link, color=config['config']['color'], appname=config['config']['appname'], repo=config['config']['repo'], url=config['config']['url'], urlDesc=urlDesc, urlUser=urlUser, urlLink=urlLink)
    else:
        return redirect(vnf['url'], 301)

def o_embed_gen(description, user, video_link):
    out = {
            "type":"video",
            "version":"1.0",
            "provider_name":"TwitFix",
            "provider_url":"https://github.com/robinuniverse/twitfix",
            "title":description,
            "author_name":user,
            "author_url":video_link
            }

    return out

if __name__ == "__main__":
    app.run(host='0.0.0.0')
