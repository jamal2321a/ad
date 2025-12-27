from datasource import DevBrawlAPI
from db import Database
import json
import time
import random
from datetime import datetime, timezone
import threading
from typing import Tuple, List, Dict, Any
import argparse
import os


class DevBrawlManager():
    """
    1. Get an unchecked player
    2. Get the battlelog of that player
    3. For each battle in the battlelog:
    4. Get all the players in the battle
    5. For each player in the battle:
    6. Check if player can do ranked
    7. If yes, add players to the database
    8. If yes, add battle to the database
    9. If no, skip the battle
    10. Mark the player as checked
    11. Repeat
    """
    def __init__(self, date):
        self.api = DevBrawlAPI()
        self.db = Database()
        random.seed(datetime.now().timestamp())
        self.first_player = "9G8GGVJ2"  # Godz 🌙
        self.best_players = [
            "9PVG0QCYU", "9U8VG0CY0", "GL9QGYRP8", "8GP2GPL8U",
            "VV2G80G", "8QVCPG9L", "9P00Q0GR", "2J2QCGU",
            "Q88JJC8VG", "PUL9GUJ8G", "PVRR9UUQL", "29P0QG92U",
            "88G8G0UJL", "88CGP8RVC", "2YUYPULV", "PUY900G",
            "82U8PL8UV", "G20YV8LY8", "999LLGGLL", "9JPRPRGY9",
            "PG8RJL0VY", "YRYG89UP", "J80C8Q0Y", "92G0UJGG0",
            "98C9YRUV", "2JQYYCJ0", "C0VGLG9V", "8UJQLUYJ",
            "2YLPY22VQ", "UUUJ80G", "CGL28CQ", "8LCRUVVL",
            "YYJQRUUJY", "L2L99RCQP", "L9Y2CQJ", "80PVL9R9U",
            "808L28CL", "98QP2QJ", "28PRYCY2U", "RYPLVJVL9",
            "U9JYCJCL", "9QCJGPL2V", "2J9LRC9", "J8LL8VGV",
            "9YCG0G02", "YG02QJVJJ", "8UL22V22", "JVPQUU0P",
            "89U0JQJP2", "2YJG2YVQ8", "8092CPQRY", "2UUJGUJGG",
            "L9VYC20R2", "GRCRQUGVC"
        ]

        time_step = time.mktime(datetime.strptime(date, "%d.%m.%Y").timetuple())
        self.min_timestamp = datetime.fromtimestamp(time_step,
                                                    tz=timezone.utc)

    def push_battle(self, battle: Tuple):
        self.db.insert_battle(battle)

    def get_unchecked_player(self, count: int = 1):
        # Modify this to include more players, not just unchecked ones
        return self.db.get_players_for_expansion(count)

    @staticmethod
    def get_players_from_battlelog(battle: Dict[str, Any]):
        players = []
        for team in battle["battle"]["teams"]:
            for player in team:
                if player["brawler"]["trophies"] >= 15:
                    players.append((player["tag"][1:], player["name"]))
        return players

    @staticmethod
    def check_ranked_eligibility(player_stats):
        brawlers = player_stats["brawlers"]
        count = 0
        for brawler in brawlers:
            if brawler["power"] >= 9:
                count += 1
        return count >= 12

    def get_player_stats(self, player_tags):
        stats = []
        batch_size = 40
        batches = []
        player_count = len(player_tags)
        for i in range(batch_size):
            start = i * player_count // batch_size
            end = (i + 1) * player_count // batch_size
            batches.append(player_tags[start:end])

        threads = [PlayerStatsThread(batch, self.api) for batch in batches]
        for thread in threads:
            thread.start()
            time.sleep(1 / batch_size)
        for thread in threads:
            thread.join()
        for thread in threads:
            stats += thread.result
        return stats

    @staticmethod
    def parse_iso_timestamp(timestamp_str):
        return (datetime.strptime(timestamp_str,
                                 "%Y%m%dT%H%M%S.%fZ")
                .replace(tzinfo=timezone.utc))

    def get_battlelogs(self, player_tags):
        logs = []
        player_tags_to_remove = []
        batch_size = 40
        batches = []
        player_count = len(player_tags)
        for i in range(batch_size):
            start = i * player_count // batch_size
            end = (i + 1) * player_count // batch_size
            batches.append(player_tags[start:end])

        threads = [BattleLogsThread(batch, self.api) for batch in batches]
        for thread in threads:
            thread.start()
            time.sleep(1 / batch_size)
        for thread in threads:
            thread.join()
        for thread in threads:
            logs += thread.result
            player_tags_to_remove += thread.player_tags_to_remove
        return logs, player_tags_to_remove

    @staticmethod
    def check_battle_with_only_eligible_players(battle,
                                                eligible_players):
        try:
            for team in battle["battle"]["teams"]:
                for player in team:
                    if player["tag"][1:] in eligible_players:
                        continue
                    else:
                        return False
            return True
        except Exception as e:
            print(e)
            print(json.dumps(battle))
            return False

    @staticmethod
    def check_rank_requirement(battle):
        return sum(int(player["brawler"]["trophies"]) >= 15 for team
                   in battle["battle"]["teams"] for player in team) >= 5

    def cycle(self):
        try:
            batch_size = 200
            players_list = self.get_unchecked_player(batch_size)
            player_tags = [player[0] for player in players_list]

            start_time = time.time()
            battle_logs, player_tags_to_remove = self.get_battlelogs(player_tags)
            for player_tag in player_tags_to_remove:
                self.db.delete_player(player_tag)

            players_with_valid_trophies = set()

            for log in battle_logs:
                for battle in log["items"]:
                    if battle["battle"].get("type") == "soloRanked":
                        for team in battle["battle"]["teams"]:
                            for player in team:
                                player_tag = player["tag"][1:]
                                if player_tag in player_tags:
                                    if player["brawler"]["trophies"] >= 15:
                                        players_with_valid_trophies.add(player_tag)

            players_to_remove = set(player_tags) - players_with_valid_trophies - set(player_tags_to_remove)
            for player_tag in players_to_remove:
                self.db.delete_player(player_tag)
                player_tags_to_remove.append(player_tag)

            print(f"Players meeting trophy requirements: {len(players_with_valid_trophies)}")
            print(f"Additional players removed (trophy filter): {len(players_to_remove)}")

            player_tags = list(players_with_valid_trophies)

            print(f"Battlelogs took: {time.time() - start_time:.2f} seconds")
            battles = [battle for log in battle_logs for battle in log["items"]]
            new_players = []
            filtered_battles = []

            for battle in battles:
                if (battle["battle"].get("type") == "soloRanked" and
                        self.parse_iso_timestamp(battle["battleTime"]) >= self.min_timestamp):
                    filtered_battles.append(battle)
                    new_players.extend(self.get_players_from_battlelog(battle))

            new_players = list(set(new_players))
            new_player_tags = [player[0] for player in new_players]
            existing_players = self.db.get_if_players_exist(new_player_tags)
            new_player_tags = list(set(new_player_tags) - set(existing_players))

            player_stats_list = self.get_player_stats(new_player_tags)
            eligible_players = [stats for stats in player_stats_list
                                if self.check_ranked_eligibility(stats)]

            print(f"Time taken: {time.time() - start_time:.2f} seconds")
            print(f"Players: {len(player_stats_list)}")
            print(f"Eligible Players: {len(eligible_players)}")
            print(f"Battles: {len(filtered_battles)}")

            real_battles = []
            for battle in filtered_battles:
                if self.check_rank_requirement(battle):
                    real_battles.append(battle)

            print(f"Real Battles: {len(real_battles)}")

            self.db.insert_many_players(eligible_players)
            for battle in real_battles:
                battle_id = random.randint(0, 10000000000000)
                unix_time = time.mktime(
                    time.strptime(battle["battleTime"], "%Y%m%dT%H%M%S.000Z"))
                unix_time = int(unix_time)
                sql_battle = (battle_id, unix_time, battle["event"]["map"],
                              battle["battle"]["mode"],
                              battle["battle"]["teams"][0][0]["brawler"]["name"],
                              battle["battle"]["teams"][0][1]["brawler"]["name"],
                              battle["battle"]["teams"][0][2]["brawler"]["name"],
                              battle["battle"]["teams"][1][0]["brawler"]["name"],
                              battle["battle"]["teams"][1][1]["brawler"]["name"],
                              battle["battle"]["teams"][1][2]["brawler"]["name"],
                              1 if battle["battle"]["result"] == "victory" else 0)
                self.push_battle(sql_battle)
            self.db.set_many_players_checked(player_tags)
            self.db.commit()

        except Exception as e:
            print(f"Error in cycle: {str(e)}")
            self.db.rollback()

    def feed_db(self):
        print(f"Currently there are {self.db.get_battles_count()} "
              f"battles in the database")
        print(f"Currently there are {self.db.get_players_count()} "
              f"players in the database")

        start_time = time.time()
        if self.db.get_players_count() == 0:
            player_api = self.api.get_player_stats(self.first_player)
            player = (player_api["tag"][1:], player_api["name"])
            self.db.insert_player(player)

        runtime = 120
        while (time.time() - start_time) < 60 * runtime and self.db.get_checked_players_percentage() < 0.98 and self.db.get_battles_count() <= 275000:
            print(f"Time left: "
                  f"{(60 * runtime - (time.time() - start_time)) / 60:.2f} "
                  f"min")
            self.cycle()
            print(f"Checked players: "
                  f"{self.db.get_checked_players_percentage()}")
            print(f"Battles in database: {self.db.get_battles_count()}")
            print(f"Players in database: {self.db.get_players_count()}")


class PlayerStatsThread(threading.Thread):

    def __init__(self, batch: List[str], api: DevBrawlAPI):
        super().__init__()
        self.batch = batch
        self.api = api
        self.result = []

    def run(self):
        for player in self.batch:
            for _ in range(3):
                stats_value = self.api.get_player_stats(player)
                if stats_value is not None:
                    self.result.append(stats_value)
                    break
                time.sleep(0.2)


class BattleLogsThread(threading.Thread):
    def __init__(self, batch: List[str], api: DevBrawlAPI):
        super().__init__()
        self.batch = batch
        self.api = api
        self.result = []
        self.player_tags_to_remove = []

    def run(self):
        for player_tag in self.batch:
            for _ in range(3):
                logs_value = self.api.get_player_battlelog(player_tag)
                if logs_value == "notFound":
                    self.player_tags_to_remove.append(player_tag)
                    break
                elif logs_value is not None:
                    self.result.append(logs_value)
                    break
                else:
                    time.sleep(0.2)


def set_last_update(date):
    with open(os.path.join("./out", 'last_update.json'), 'w') as f:
        json.dump(date, f)


def get_last_update():
    with open(os.path.join("./out", 'last_update.json'), 'r') as f:
        return json.load(f)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Brawl Stars Data Collection')
    parser.add_argument('--last_update',
                        type=str,
                        help='Date for data collection (format: DD.MM.YYYY)')

    args = parser.parse_args()
    date = ""
    print(args)
    if args.last_update:
        set_last_update(args.last_update)
        date = args.last_update
        print(date)
    else:
        date = get_last_update()

    manager = DevBrawlManager(date)
    if manager.db.get_players_count() == 0:
        for player in manager.best_players:
            player_stats = manager.api.get_player_stats(player)
            if player_stats:
                player = (player_stats["tag"][1:], player_stats["name"])
                manager.db.insert_player(player)

    manager.feed_db()
