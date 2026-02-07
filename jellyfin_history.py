import requests
from collections import defaultdict
import json
import os
from datetime import datetime
import sys

# -----------------------------
# CONFIGURATION
# -----------------------------
JELLYFIN_URL = "https://nl5167.dediseedbox.com:5393/"
API_KEY = "526e18a144e64113852a5caa9c6d1558"

HEADERS = {
    "X-Emby-Token": API_KEY
}

# File to store previous watched status
WATCHED_HISTORY_FILE = "jellyfin_watched_history.json"

# -----------------------------
# WATCHED HISTORY MANAGEMENT
# -----------------------------
def load_watched_history():
    if os.path.exists(WATCHED_HISTORY_FILE):
        with open(WATCHED_HISTORY_FILE, 'r') as f:
            data = json.load(f)
            # Convert lists back to sets
            return {uid: set(items) for uid, items in data.get('watched', {}).items()}, data.get('last_run')
    return {}, None

def save_watched_history(watched_by_user, timestamp=None):
    if timestamp is None:
        timestamp = datetime.now().isoformat()
    data = {
        'watched': {uid: list(items) for uid, items in watched_by_user.items()},
        'last_run': timestamp
    }
    with open(WATCHED_HISTORY_FILE, 'w') as f:
        json.dump(data, f, indent=2)

# -----------------------------
# API HELPERS
# -----------------------------
def api(path, params=None):
    url = f"{JELLYFIN_URL}{path}"
    r = requests.get(url, headers=HEADERS, params=params)
    r.raise_for_status()
    return r.json()

# -----------------------------
# FETCH USERS
# -----------------------------
def get_users():
    users = api("/Users")
    return {u["Id"]: u["Name"] for u in users}

# -----------------------------
# FETCH LIBRARY ITEMS
# -----------------------------
def get_all_items():
    params = {
        "IncludeItemTypes": "Episode,Movie",
        "Recursive": True,
        "Fields": "SeriesName,SeasonName,ParentIndexNumber,IndexNumber"
    }
    return api("/Items", params)["Items"]

# -----------------------------
# FETCH WATCHED STATUS PER USER
# -----------------------------
def get_user_watched(user_id):
    params = {
        "IncludeItemTypes": "Episode,Movie",
        "Recursive": True,
        "Filters": "IsPlayed"
    }
    items = api(f"/Users/{user_id}/Items", params)["Items"]
    return {item["Id"] for item in items}

# -----------------------------
# MAIN LOGIC
# -----------------------------
def build_report(mode, users, content_type, view_mode):
    print(f"Found {len(users)} users: {list(users.values())}")

    # Watched sets per user
    watched_by_user = {
        uid: get_user_watched(uid)
        for uid in users.keys()
    }

    # Load previous watched if in delta mode
    if mode == 'delta':
        prev_watched, last_run = load_watched_history()
        print(f"Last run: {last_run}")
    else:
        prev_watched = {}

    # All items in library (only needed for full mode or to get names)
    if mode == 'full':
        items = get_all_items()
    else:
        # For delta, we might not need all items, but for reporting names, we do
        # To optimize, perhaps fetch only new items, but for simplicity, fetch all
        items = get_all_items()

    # Data structures
    movies = {}
    shows = defaultdict(lambda: defaultdict(dict))

    for item in items:
        item_id = item["Id"]
        item_name = item["Name"]

        # MOVIES
        if item["Type"] == "Movie" and content_type in ['movies', 'both']:
            watched_by = [
                users[uid] for uid, watched in watched_by_user.items()
                if item_id in watched
            ]
            if mode == 'delta':
                prev_watchers = [
                    users[uid] for uid, watched in prev_watched.items() if uid in users and item_id in watched
                ]
                new_watchers = [u for u in watched_by if u not in prev_watchers]
                if not new_watchers:
                    continue  # No new watches for this movie
                watched_by = new_watchers  # Only show new watchers

            movies[item_id] = {
                "title": item_name,
                "watched_by": watched_by
            }

        # EPISODES
        elif item["Type"] == "Episode" and content_type in ['shows', 'both']:
            series = item.get("SeriesName", "Unknown Series")
            season = item.get("ParentIndexNumber", 0)
            episode = item.get("IndexNumber", 0)

            watched_by = [
                users[uid] for uid, watched in watched_by_user.items()
                if item_id in watched
            ]
            if mode == 'delta':
                prev_watchers = [
                    users[uid] for uid, watched in prev_watched.items() if uid in users and item_id in watched
                ]
                new_watchers = [u for u in watched_by if u not in prev_watchers]
                if not new_watchers:
                    continue  # No new watches
                watched_by = new_watchers

            shows[series][season][episode] = {
                "title": item_name,
                "id": item_id,
                "watched_by": watched_by
            }

    # Save current watched for next delta run
    if mode == 'delta':
        save_watched_history(watched_by_user)

    return movies, shows

# -----------------------------
# PRINT REPORT
# -----------------------------
def print_report(movies, shows, users, content_type, view_mode, mode='full'):
    all_usernames = list(users.values())
    
    output_lines = []

    if mode == 'delta':
        output_lines.append("\n==================== NEW WATCHES SINCE LAST RUN ====================")
    else:
        output_lines.append("\n==================== FULL REPORT ====================")

    if content_type in ['movies', 'both']:
        output_lines.append("\n==================== MOVIES ====================")
        for mid, m in movies.items():
            if view_mode == 'watched' and len(m['watched_by']) != len(all_usernames):
                continue
                
            output_lines.append(f"\n🎬 {m['title']}")
            output_lines.append(f"   Watched by: {', '.join(m['watched_by']) or 'Nobody'}")
            
            if len(m['watched_by']) == len(all_usernames):
                output_lines.append("    ✅ All users have watched this movie")

    if content_type in ['shows', 'both']:
        output_lines.append("\n==================== TV SHOWS ====================")
        for series, seasons in shows.items():
            series_has_watched_items = False
            series_lines = [f"\n📺 {series}"]
            
            for season, episodes in sorted(seasons.items()):
                season_has_watched_items = False
                season_lines = [f"  Season {season}"]

                # Check if all episodes watched by all users
                all_watched = True

                for ep_num, ep in sorted(episodes.items()):
                    watchers = ep["watched_by"]
                    missing = [u for u in all_usernames if u not in watchers]

                    if view_mode == 'watched' and len(watchers) != len(all_usernames):
                        if missing:
                            all_watched = False
                        continue
                    
                    season_has_watched_items = True
                    series_has_watched_items = True
                    
                    season_lines.append(f"    Episode {ep_num}: {ep['title']}")
                    season_lines.append(f"      Watched by: {', '.join(watchers) or 'Nobody'}")
                    
                    if len(watchers) == len(all_usernames):
                        season_lines.append("        ✅ All users have watched this episode")

                    if missing:
                        all_watched = False

                if season_has_watched_items or view_mode == 'full':
                    series_lines.extend(season_lines)
                    
                    if all_watched and view_mode == 'full':
                        series_lines.append("    ✅ All users have watched this season")
                    elif not all_watched and view_mode == 'full':
                        series_lines.append("    ❌ Not all users have watched this season")

            if series_has_watched_items or view_mode == 'full':
                output_lines.extend(series_lines)

                # Check if entire series is watched by all users
                if view_mode == 'full':
                    series_fully_watched = all(
                        all(
                            set(ep["watched_by"]) == set(all_usernames)
                            for ep in episodes.values()
                        )
                        for episodes in seasons.values()
                    )

                    if series_fully_watched:
                        output_lines.append("  ⭐ Entire series watched by all users")
                    else:
                        output_lines.append("  — Series not fully watched by all users")

    # Print to console
    for line in output_lines:
        print(line)
    
    return output_lines

def export_report(output_lines, filename="jellyfin_report.txt"):
    """Export the report to a text file"""
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            for line in output_lines:
                f.write(line + '\n')
        print(f"\nReport exported to {filename}")
    except Exception as e:
        print(f"Error exporting report: {e}")

def select_options(users):
    # Ask for mode
    print("\nWhat type of report do you want?")
    print("  - 'full' for a complete report of all watched content")
    print("  - 'delta' for only new watches since last run")
    
    mode = input("\nSelect mode: ").strip().lower()
    while mode not in ['full', 'delta']:
        print("Invalid choice. Please enter 'full' or 'delta'.")
        mode = input("\nSelect mode: ").strip().lower()

    print("\nAvailable users:")
    for i, (uid, name) in enumerate(users.items(), start=1):
        print(f"  {i}. {name}")

    print("\nType:")
    print("  - a single number (e.g., 2)")
    print("  - multiple numbers separated by commas (e.g., 1,3,4)")
    print("  - 'all' to include every user")

    choice = input("\nSelect users: ").strip().lower()

    if choice == "all":
        selected_users = users  # no filtering
    else:
        # Parse numeric selections
        selected_users = {}
        indices = [c.strip() for c in choice.split(",")]

        for idx in indices:
            if not idx.isdigit():
                print(f"Invalid entry: {idx}")
                continue

            idx = int(idx)
            if 1 <= idx <= len(users):
                uid = list(users.keys())[idx - 1]
                selected_users[uid] = users[uid]
            else:
                print(f"User number {idx} is out of range")

        if not selected_users:
            print("No valid users selected — defaulting to ALL users.")
            selected_users = users

    # Ask for content type
    print("\nWhat content do you want to analyze?")
    print("  - 'movies' for movies only")
    print("  - 'shows' for TV shows only") 
    print("  - 'both' for movies and TV shows")
    
    content_choice = input("\nSelect content type: ").strip().lower()
    while content_choice not in ['movies', 'shows', 'both']:
        print("Invalid choice. Please enter 'movies', 'shows', or 'both'.")
        content_choice = input("\nSelect content type: ").strip().lower()

    # Ask for view mode
    print("\nHow would you like to view the results?")
    print("  - 'full' to show all items with watch status")
    print("  - 'watched' to show only items watched by all selected users")
    
    view_choice = input("\nSelect view mode: ").strip().lower()
    while view_choice not in ['full', 'watched']:
        print("Invalid choice. Please enter 'full' or 'watched'.")
        view_choice = input("\nSelect view mode: ").strip().lower()

    return mode, selected_users, content_choice, view_choice

# -----------------------------
# RUN
# -----------------------------
if __name__ == "__main__":
    # Parse arguments: python script.py [mode] [users] [content_type] [view_mode]
    # mode: full or delta
    # users: all or comma-separated indices
    # content_type: movies, shows, both
    # view_mode: full, watched
    args = sys.argv[1:]
    if args and len(args) >= 4 and args[0] in ['full', 'delta']:
        # Non-interactive mode
        mode = args[0]
        user_choice = args[1]
        content_type = args[2]
        view_mode = args[3]
        
        all_users = get_users()
        if user_choice == "all":
            users = all_users
        else:
            users = {}
            indices = [int(x.strip()) for x in user_choice.split(',') if x.strip().isdigit()]
            for idx in indices:
                if 1 <= idx <= len(all_users):
                    uid = list(all_users.keys())[idx - 1]
                    users[uid] = all_users[uid]
            if not users:
                users = all_users
        
        movies, shows = build_report(mode, users, content_type, view_mode)
        output_lines = print_report(movies, shows, users, content_type, view_mode, mode)
        
        # Always export to file in non-interactive mode
        filename = f"jellyfin_report_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        export_report(output_lines, filename)
    else:
        # Interactive mode
        all_users = get_users()
        mode, users, content_type, view_mode = select_options(all_users)
        movies, shows = build_report(mode, users, content_type, view_mode)
        output_lines = print_report(movies, shows, users, content_type, view_mode, mode)
        
        # Ask about exporting
        export_choice = input("\nWould you like to export this report to a file? (y/n): ").strip().lower()
        if export_choice in ['y', 'yes']:
            filename = input("Enter filename (default: jellyfin_report.txt): ").strip()
            if not filename:
                filename = "jellyfin_report.txt"
            export_report(output_lines, filename)
