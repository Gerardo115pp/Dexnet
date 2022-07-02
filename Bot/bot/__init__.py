from os import getenv, path
import discord, requests
from typing import List, Dict
import asyncio, aiohttp
import json
from dataclasses import dataclass, field, asdict
import shlex, argparse

BOT_DATA_PATH_ENVAR = "BOT_DATA_PATH"

@dataclass
class Project:
    id:int
    name: str
    clickup_id: int
    github_repo_name: str
    assignees: List[str] = field(default_factory=list)
    
    def asdict(self):
        return asdict(self)

@dataclass
class TeamMember:
    username: str # Discord username
    clickup_id: str
    github_user_account: str
    projects:List[int] = field(default_factory=list)
    
    def asdict(self):
        return asdict(self)
    
    

class DiscordBot(discord.Client):
    
    def __init__(self, token) -> None:
        super().__init__()
        self.bot_name = getenv("BOT_NAME") 
        self.passphrase = getenv("PASSPHRASE", "")
        self.admin_passphrase = getenv("ADMIN_PASSPHRASE", "")
        self.projects: Dict[str, Project] = {}
        self.team_members: Dict[int, List[TeamMember]] = {}
        assert self.passphrase, "PASSPHRASE is not set"
        assert self.admin_passphrase, "ADMIN_PASSPHRASE is not set"
        
        self.data_path = getenv(BOT_DATA_PATH_ENVAR)
        self.config = {
            "servers_data": {}
        }
        
        self.credentials = {
            "github_token": getenv("GITHUB_TOKEN"),
            "github_user": getenv("GITHUB_USER"),
            "clickup_token": getenv("CLICKUP_TOKEN")
        }
        
        assert self.credentials["github_token"], "GITHUB_TOKEN is not set"
        assert self.credentials["clickup_token"], "CLICKUP_TOKEN is not set"
        
        self.__token = token
        
        self.loadBotData()

    @property
    def CommandPrefix(self) -> str:
        return f"${self.bot_name} "
    
    def commandAssignTask(self, args:argparse.Namespace) -> str:
        if not args.assign:
            return "No assignee specified"

        clickup_task_api = f"https://api.clickup.com/api/v2/task/{args.task_id}"
        clickup_task_headers = {
            "Authorization": f"{self.ClickUpToken}",
            "Content-Type": "application/json"
        }
        
        task_data = {
            "assignees": {
                "add": args.assign
            }
        }
        
        response = requests.put(clickup_task_api, headers=clickup_task_headers, json=task_data)

        if response.ok:
            return f"Task {args.task_id} assigned to {args.assign}"
        else:
            return f"Error assigning task {args.task_id} to {args.assign}: {response.text}"
        
    async def commandAddDeveloper(self, project_name:str, github_user:str, message:discord.Message):
        github_user_data = self.getGithubUserData(github_user)
        if github_user_data is None:
            await message.channel.send(f"```arm\n'{github_user}' user does not exist on GitHub\n```")
            return
        
        if project_name not in self.projects:
            await message.channel.send(f"```arm\n'{project_name}' project does not exist\n```")
            return

        project = self.projects[project_name]
        project.assignees.append(github_user)
        await message.reply(f"Added '{github_user_data['html_url']}' to '{project_name}'")
        self.saveProjects()    
    
    def commandCreateTask(self, args:argparse.Namespace) -> str:
        list_id = args.list_id
        clickup_api_url = f"https://api.clickup.com/api/v2/list/{list_id}/task"
        
        headers = {
            "Authorization": f"{self.ClickUpToken}",
            "Content-Type": "application/json"
        }
        
        task_data =  {
            "name": args.task_name,
            "description": args.task_description,
            "status": args.status,
            "priority": args.priority,
            "time_estimate": args.time
        }
        
        session = requests.session()
        response = session.post(clickup_api_url, headers=headers, data=json.dumps(task_data))
        session.close()
        
        if response.ok:
            return f"Task '{args.task_name}' created successfully"
        else:
            return f"Error '{response.status_code}' creating task: {response.text}"
        
    def commandClickupTeam(self) -> str:
        clickup_team_api = f"https://api.clickup.com/api/v2/team"
        clickup_team_headers = {
            "Authorization": f"{self.ClickUpToken}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(clickup_team_api, headers=clickup_team_headers)
        if response.ok:
            team_repr = "```sql\n"
            team = json.loads(response.text)["teams"]
            if not len(team):
                return "No teams found"
            
            for member in team[0]['members']:
                team_repr += f"{'id':>15}: {member['user']['id']}\n{'username':>15}: {member['user']['username']}\n{'email':>15}: {member['user']['email']}\n{'role':>15}: {member['user']['role']}\n"
                if 'invited_by' in member:
                    team_repr += f"{'invited_by':>15}: {member['invited_by']['username']}\n"
                team_repr += f"\n{'-'*30}\n"
            team_repr += "```"
            return team_repr
        else:
            return f"Error '{response.status_code}' getting team: {response.text}"
                    
    async def commandCreateFeature(self, message_obj: discord.Message, *args) -> None:
        if not self.isUserAdmin(message_obj):
            return
        
        assert len(args) >= 3, f"Invalid number of arguments: {args}"
        
        project_name, task_name, task_description = args[0], args[1], args[2]
        
        project = self.projects.get(project_name, None)
        assert project, f"Project {project_name} does not exist"
        
        clickup_id = project.clickup_id
        clickup_task = self.createClickUpTask(clickup_id, task_name, task_description)
        if self.createGithubIssue(project_name, task_name, clickup_task["id"], task_description) <= 299:
            print(f"Created Github issue {task_name}")
            await message_obj.channel.send(f"Created issue '{task_name}'")
        else:
            print("Error creating Github issue")
            await message_obj.channel.send(f"Error creating Github issue, sorry for the inconvenience")
    
    def commandCreateMember(self, args:argparse.Namespace) -> str:
        server_team_members = self.team_members.get(args.server_id, [])
        new_member = TeamMember(args.discord_username, args.member_clickup_id, args.member_github_account)
        
        server_team_members.append(new_member)
        
        self.team_members[args.server_id] = server_team_members
        return f"Added '{args.discord_username}' to the team"

    async def commandListDevelopers(self, project_name:str, message_obj: discord.Message) -> None:
        if project_name not in self.projects:
            await message_obj.channel.send(f"```arm\n'{project_name}' project does not exist\n```")
            return
        
        developers = self.getDevelopers(project_name)
        developers_str = "\n".join([f"- {developer}" for developer in developers])
        await message_obj.channel.send(f"```yaml\n{developers_str}\n```")
    
    def commandGetListMemebers(self, args:argparse.Namespace) -> str:
        list_id = args.list_id
        clickup_api_url = f"https://api.clickup.com/api/v2/list/{list_id}/member"
        headers = {
            "Authorization": f"{self.ClickUpToken}",
            "Content-Type": "application/json"
        }
        
        session = requests.session()
        response = session.get(clickup_api_url, headers=headers)
        session.close()
        
        if response.ok:
            memebers = json.loads(response.text).get("members", [])
            list_message = "```sql\n"
            list_message += "\n".join([f"{member['username']} - id: {member['id']} - email: {member['email']}" for member in memebers])
            list_message += "\n```"
            return list_message
        else:
            return f"Error '{response.status_code}' getting list members: {response.text}"
    
    async def commandListIssues(self, project_name:str, message_obj: discord.Message) -> None:
        issues_data = self.getProjectIssues(project_name)
        issues_list_message_content = ""
        for issue in issues_data:
            issues_list_message_content += f"-> {issue['title']} - id:{issue['number']} - state:{issue['state']} - assignees:"
            for assignee in issue['assignees']:
                issues_list_message_content += f" {assignee['login']},"
            issues_list_message_content = issues_list_message_content[:-1] + "\n\n"
        
        await message_obj.channel.send(f"```yaml\n{issues_list_message_content}\n```")
    
    def commandListProjects(self) -> str:
        projects_str = "```sql\n"
        for project in self.projects.values():
            projects_str += f"{'name':>15}: {project.name}\n{'clickup_id':>15}: {project.clickup_id}\n{'github_repo':>15}: {project.github_repo_name}\n{'-'*40}\n"
        
        projects_str += "```"
        return projects_str
    
    def commandListProjectTasks(self, args:argparse.Namespace) -> str:
        project_name = args.project_name
        
        if project_name not in self.projects:
            return f"```arm\n'{project_name}' project does not exist\n```"

        list_id = self.projects[project_name].clickup_id
        clickup_api_url = f"https://api.clickup.com/api/v2/list/{list_id}/task"
        
        headers = {
            'Authorization': f"{self.ClickUpToken}",
            'Content-Type': 'application/json'
        }
        
        response = requests.get(clickup_api_url, headers=headers)
        
        if response.ok:
            message_content = "```sql\n"
            
            for task in json.loads(response.text).get("tasks", []):
                time_estimate = task.get("time_estimate", None)
                time_estimate = time_estimate/1000 if time_estimate else 0
                priority = "none"
                if task.get("priority", None):
                    priority = task['priority'].get('priority', 'none')
                message_content += f"{'name':>15}: {task['name']:>24}\t\n{'id':>15}: {task['id']:>24}\t\n{'status':>15}: {task['status']['status']:>24}\t\n{'priority':>15}: {priority:>24}\t\n{'time_estimate':>15}: {time_estimate:>24}\t\n{'assignees':>15}: "
                assignees = task.get('assignees', [])
                assignees = '\n'.join([assignee['username'] for assignee in assignees])
                message_content += f" {assignees:>24}\n{'-'*80}\n"
            
            message_content += "```"
            return message_content
        else:
            return f"Error '{response.status_code}' getting list tasks: {response.text}"
        
    async def commandSaveClickUpList(self, list_id:int, message_obj: discord.Message) -> None:
        clickup_url = f"https://api.clickup.com/api/v2/list/{list_id}"
        
        session = requests.session()
        headers = {
            'Authorization': self.ClickUpToken,
            'Content-Type': 'application/json'
        }
        
        response = session.get(clickup_url, headers=headers)
        
        if response.ok and self.config["servers_data"].get(str(message_obj.guild.id), False):
            list_data = json.loads(response.text)
            self.config["servers_data"][f"{message_obj.guild.id}"]["click_up"]["lists"].append(list_data)
            self.saveConfig()
            
            await message_obj.channel.send(f"Saved list {list_data['name']}")
        elif response.status_code == 404:
            await message_obj.channel.send(f"```arm\nList {list_id} does not exist\n```")
        else:
            print(f"Error saving list {list_id}: {response.status_code} - {clickup_url}")
            await message_obj.channel.send(f"```arm\nError saving list {list_id}\n```")
        
        return
    
    async def commandListClickUpLists(self, message_obj: discord.Message) -> None:
        if not self.config["servers_data"].get(str(message_obj.guild.id), False):
            await message_obj.channel.send(f"```arm\nNo lists saved\n```")
            return

        discord_server = str(message_obj.guild.id)
        clickup_lists = self.config["servers_data"][discord_server]["click_up"]["lists"]
        
        clickup_lists_messages = f"```yaml\n"
        for list_data in clickup_lists:
            clickup_lists_messages += f"{list_data['name']} - id:{list_data['id']}\n"
        clickup_lists_messages += "```"
        
        await message_obj.channel.send(clickup_lists_messages)
    
    @property
    def ClickUpToken(self) -> str:
        return self.credentials["clickup_token"]
    
    def createProject(self, project_name:str, clickup_id:int, github_repo_name:str) -> None:
        self.projects[project_name] = Project(len(self.projects)+1, project_name,  clickup_id, github_repo_name)
        self.saveProjects()
        return
    
    def createClickUpTask(self, list_id:str, task_name:str, task_desc:str) -> Dict:
        tasks_url = f"https://api.clickup.com/api/v2/list/{list_id}/task"
        form_data = {
            "name": task_name,
            "description": task_desc,
            "status": "Open",
            "priority": 3,
            "time_estimate": (60 * 60) * 1000
        }
        headers = {
            "Authorization": self.ClickUpToken,
            "Content-Type": "application/json"
        }
        
        response = requests.post(tasks_url, json=form_data, headers=headers)
        return_data = {}
        if response.status_code < 300:
            return_data = json.loads(response.text)
        
        return return_data
    
    def createGithubIssue(self, project_name:str, issue_name:str, task_id:str, issue_body:str) -> None:
        assert project_name in self.projects, f"Project {project_name} does not exist"
        
        github_repo_name = self.projects[project_name].github_repo_name
        github_user = self.credentials["github_user"]
        github_issue_url = f"https://api.github.com/repos/{github_user}/{github_repo_name}/issues"
        print("Github issue url:", github_issue_url)
        
        session = requests.session()
        session.auth = (self.credentials["github_user"], self.credentials["github_token"])
        
        issue_data = {
            "title": issue_name,
            "body": f"This issue is created from ClickUp Task #{task_id}: {issue_body}",
            "labels": ["feature", "clickup"]
        }
        
        response = session.post(github_issue_url, json=issue_data)
        session.close()
        
        return response.status_code
    
    def enableChannel(self, guild_id: str, channel_id: str) -> None:
        guild_id = guild_id if type(guild_id) is str else str(guild_id)
        channel_id = channel_id if type(channel_id) is str else str(channel_id)
        enabled_channels = self.Servers[guild_id]["channels"]
            
        if channel_id in enabled_channels:
            enabled_channels[channel_id]["status"] = True
        else:
            new_channel = self.get_channel(int(channel_id))
            self.Servers[guild_id]["channels"][channel_id] = {
                "name": new_channel.name,
                "status": True
            }
            
        self.saveConfig()

    async def enableAdmin(self, message_obj: discord.Message) -> None:
        new_admin = message_obj.author
        admin_array = self.Servers[str(message_obj.guild.id)]["admins"]

        if new_admin.id in admin_array:
            await message_obj.channel.send("You are already an admin")
            return
        
        admin_array.append(new_admin.id)
        await message_obj.channel.send("You are now an admin", delete_after=1560)
        self.saveConfig()

    @property
    def GitHubToken(self) -> str:
        return self.credentials["github_token"]

    @property
    def GitHubUser(self) -> str:
        return self.credentials["github_user"]

    def getGithubUserData(self, user_name:str) -> Dict:
        session = requests.session()
        session.auth = (self.GitHubUser, self.GitHubToken)
        
        github_user_url = f"https://api.github.com/users/{user_name}"
        response = session.get(github_user_url)
        session.close()
        
        if response.status_code == 404:
            return None

        user_data = json.loads(response.text)
        
        return user_data
    
    def getDevelopers(self, project_name:str) -> List:
        assert project_name in self.projects, f"Project {project_name} does not exist"
        
        developers = self.projects[project_name].assignees
        return developers
    
    def getProjectIssues(self, project_name:str) -> List:
        if project_name not in self.projects:
            print(f"Project {project_name} does not exist")
            return []
        
        github_repo_name = self.projects[project_name].github_repo_name
        github_issues_url = f"https://api.github.com/repos/{self.GitHubUser}/{github_repo_name}/issues"
        print("Github issues url:", github_issues_url)
        
        session = requests.session()
        session.auth = (self.GitHubUser, self.GitHubToken)
        
        response = session.get(github_issues_url)
        session.close()
        
        issues_data = []
        if response.status_code != 404:
            issues_data = json.loads(response.text)
        
        return issues_data

    @property
    def Help(self) -> str:
        """ Shows help message for non-admin commands """
        help_message = f"{self.bot_name} commands:\n"
        help_message += '''
        ${bot_name} status - check if the bot is enabled in this channel
        ${bot_name} list-projects - list all projects
        ${bot_name} project-tasks - list all tasks for a project
        '''
        
        return help_message
    
    def isChannelEnabled(self, guild_id: str, channel_id: str) -> bool:
        is_enabled = False
        guild_id = guild_id if type(guild_id) is str else str(guild_id)
        channel_id = channel_id if type(channel_id) is str else str(channel_id)
        
        
        if guild_id in self.Servers:
            if channel_id in self.Servers[guild_id]["channels"]:
                is_enabled = self.Servers[guild_id]["channels"][channel_id]["status"]
        
        return is_enabled
    
    def isUserAdmin(self, message_obj) -> bool:
        return message_obj.author.id in self.Servers[str(message_obj.guild.id)]["admins"]
    
    def loadBotData(self) -> None:
        if path.exists(path.join(self.data_path, "config.json")):
            print("Loading config file")
            self.loadConfig()
            
        if path.exists(path.join(self.data_path, "projects.json")):
            print("Loading projects file")
            self.loadProjects()
        
        if path.exists(path.join(self.data_path, "team_members.json")):
            print("Loading team members file")
            self.loadTeamMembers()
    
    def loadTeamMembers(self) -> None:
        team_members_file = path.join(self.data_path, "team_members.json")
        
        if not path.exists(team_members_file):
            return
        

        with open(team_members_file, "r") as f:
            team_members = json.load(f)
            
            for member in team_members:
                self.team_members.append(TeamMember(**member))
        
    def loadConfig(self) -> None:
        config_file = path.join(self.data_path, "config.json")
        if not path.exists(config_file):
            return
        
        loaded_config = {}
        with open(config_file) as f:
            loaded_config = json.load(f)
        
        for key in self.config:
            if key in loaded_config:
                self.config[key] = loaded_config[key]
        
        return
    
    def loadProjects(self) -> None:
        projects_file = path.join(self.data_path, "projects.json")
        
        if not path.exists(projects_file):
            return

        projects_data = {}
        with open(projects_file) as f:
            projects_data = json.load(f)
        
        for project_name, project_data in projects_data.items():
            self.projects[project_name] = Project(**project_data)
        
        print(f"loaded {len(self.projects)} projects")
        return
    
    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user:
            return
        
        if self.isChannelEnabled(message.guild.id, message.channel.id):
            print(f"RECEIVED MESSAGE: from guild '{message.guild.name}' for active channel '{message.channel.name}:\t{message.content}")
        
        if message.content.startswith("$"):
            print(f"Received command from {message.guild.name} by {message.author.name} in channel {message.channel.name}")
            
            if message.content.startswith(f"${self.bot_name} status"):
                response:str = f"Channel {message.channel.name} is: {'enabled' if self.isChannelEnabled(message.guild.id, message.channel.id) else 'disabled'}"
                await message.channel.send(response)
                
            elif message.content.startswith(f"${self.bot_name} enable") and self.isUserAdmin(message):
                print(f"Enabling channel {message.channel.name} by request of {message.author.name}")
                if not self.isChannelEnabled(message.guild.id, message.channel.id):
                    self.enableChannel(message.guild.id, message.channel.id)
                await message.channel.send(f"Channel {message.channel.name} is now enabled")
    
            elif self.isChannelEnabled(message.guild.id, message.channel.id):
                await self.runCommand(message.content, message)
        
        elif message.content == self.passphrase:
            print(f"ENABLE REQUEST: from guild '{message.guild.name}' for channel '{message.channel.name}'")
            await message.delete()
            if not self.isChannelEnabled(message.guild.id, message.channel.id):
                self.enableChannel(message.guild.id, message.channel.id)
                print(f"ENABLED CHANNEL: {message.channel.name}")
                await message.channel.send(f"bot commands enable for channel '{message.channel.name}' in discord server '{message.guild.name}'")

        elif message.content == self.admin_passphrase:
            print(f"ADMIN ADD REQUEST: from guild '{message.guild.name}' for channel '{message.channel.name}'")
            await message.delete()
            await self.enableAdmin(message)
            print(f"ADMIN ADDED: {message.author.name}")

    async def on_ready(self) -> None:
        changed:bool = False
        for guild in self.guilds:
            guild_id = str(guild.id)
            if guild_id not in self.Servers:
                changed = True
                
                self.Servers[guild_id] = {
                    "name": guild.name,
                    "procedures": [],
                    "channels": {},
                    "admins": [],
                    "click_up": {
                        "lists": []
                    }
                }
                
                for channel in guild.channels:
                    channel_id = str(channel.id)
                    if isinstance(channel, discord.TextChannel):
                        self.Servers[guild_id]["channels"][channel_id] = {
                            "status": False,
                            "name": channel.name
                        }
                        
        if changed:
            self.saveConfig()
        
        print(f"Bot is ready!")
    
    def parseCommand(self, command:str) -> str:
        print(f"Parsing command: {command} into {command.replace(self.CommandPrefix, '')}")
        return shlex.split(command.replace(self.CommandPrefix, ""))
    
    def run(self, *args, **kwargs):
        return super().run(self.__token, **kwargs)
    
    async def runCommand(self, command: str, message_obj: discord.Message) -> None:
        
        match self.parseCommand(command):
            case ["list-projects"]:
                print("Listing projects")
                message = self.commandListProjects()
                await message_obj.channel.send(message)
                return
            
            case ["project-tasks", *command_args] if len(command_args) > 0:
                project_tasks_parser = argparse.ArgumentParser(description="Project tasks")
                project_tasks_parser.add_argument("project_name", help="Name of the project")
                try:
                    project_tasks_args = project_tasks_parser.parse_args(command_args)
                except Exception as e:
                    await message_obj.channel.send(f"```arm\nError: {e}\n```")
                    return
                
                async with message_obj.channel.typing():
                    message = self.commandListProjectTasks(project_tasks_args)
                    await message_obj.channel.send(message)
                    
            case ["help"]:
                await message_obj.channel.send(self.Help)
            
            case other:
                await self.runAdminCommands(command, message_obj)
                return
    
    async def runAdminCommands(self, command:str, message_obj: discord.Message) -> None:
        if not self.isUserAdmin(message_obj):
            print(f"Unknown command '{command}'")
            await message_obj.reply(f"Unknown command '{command}'")
        
        match self.parseCommand(command):
            case ["create-project", *project_data] if len(project_data) == 3:
                # $dexnet create-project project_name clickup_list_id github_repo
                print(f"Creating project '{project_data[0]}' with description '{project_data[1]}' and url '{project_data[2]}'")
                self.createProject(project_data[0], project_data[1], project_data[2])
                await message_obj.channel.send(f"Project '{project_data[0]}' created")
                return
            
            case ["create-member", *member_data] if len(member_data) >= 2:
                # $dexnet create-member member_clickup_id member_github_account
                if len(message_obj.mentions) == 0:
                    await message_obj.channel.send(f"Please mention the member to add")
                    return
                
                create_member_parser = argparse.ArgumentParser(description="Create member")
                create_member_parser.add_argument("member_clickup_id", type=str, help="Clickup ID of the member")
                create_member_parser.add_argument("member_github_account", type=str, help="Github account of the member")
                try:
                    create_member_args = create_member_parser.parse_known_args(member_data)
                    create_member_args = create_member_args[0]
                    if create_member_args.member_clickup_id.startswith("<@"):
                        raise Exception("Incorrect parameters order please follow the next format: $dexnet create-member member_clickup_id member_github_account @member_mention")
                except Exception as e:
                    await message_obj.channel.send(f"```arm\nError: {e}\n```")
                    return
                
                
                create_member_args.discord_username = message_obj.mentions[0].name
                create_member_args.server_id = message_obj.guild.id
                message = self.commandCreateMember(create_member_args)
                await message_obj.channel.send(message, reference=message_obj)
                
                
            case ["new-feature", *issue_data] if len(issue_data) == 3:
                # $dexnet new-issue project_name issue_title issue_body
                print(f"Creating issue '{issue_data[0]}' in project '{issue_data[1]}' with description '{issue_data[2]}'")
                await self.commandCreateFeature(message_obj, *issue_data)
                return
            
            case ["new-dev", *dev_data] if len(dev_data) == 2:
                # $dexnet new-dev project_name github_username
                print(f"Adding dev '{dev_data[1]}' to project '{dev_data[0]}'")
                assert len(dev_data) == 2,f"Invalid number of arguments for new-dev command: {(dev_data)}"
                project_name, github_user = dev_data[0], dev_data[1]
                await self.commandAddDeveloper(project_name, github_user, message_obj)
                return

            case ["set-assignee", project_name, issue_id, github_user]:
                # $dexnet set-assignee project_name issue_id github_user
                print(f"Setting assignee for issue '{issue_id}' in project '{project_name}' to '{github_user}'")
                if self.setAssignee(project_name, issue_id, github_user):
                    await message_obj.channel.send(f"Assignee for issue '{issue_id}' in project '{project_name}' set to '{github_user}'")
                else:
                    await message_obj.channel.send(f"Assignee for issue '{issue_id}' in project '{project_name}' not set")
                return
            
            case ["list-devs", project_name]:
                # $dexnet list-devs project_name
                print(f"Listing devs for project '{project_name}'")
                await self.commandListDevelopers(project_name, message_obj)
                return
            
            case ["list-issues", project_name]:
                # $dexnet list-issues project_name
                print(f"Listing issues for project '{project_name}'")
                await self.commandListIssues(project_name, message_obj)
                
            case ["create-issue", project_name, issue_name, issue_body]:
                # $dexnet create-issue project_name issue_title issue_body
                print(f"Creating issue '{issue_name}' in project '{project_name}'")
                status_code = self.createGithubIssue(project_name, issue_name, issue_body)
                
                if status_code < 300:
                    await message_obj.channel.send(f"Issue '{issue_name}' created")
                else:
                    await message_obj.channel.send(f"Issue '{issue_name}' creation failed")
                return
            
            case ["create-task", *command_args] if len(command_args) >= 3:
                # $dexnet create-task list_id task_name task_description
                create_task_parser = argparse.ArgumentParser(description="Create a new task", usage="$dexnet create-task task_name task_description")
                create_task_parser.add_argument("list_id", type=int, help="The list id of the list to add the task to")
                create_task_parser.add_argument("task_name", type=str, help="The id of the task to create")
                create_task_parser.add_argument("task_description", type=str, help="The description of the task to create")
                create_task_parser.add_argument("-t", "--time", type=int, default=3600, help="The time in seconds to set the task to complete")
                create_task_parser.add_argument("-p", "--priority", type=int, default=3, choices=[1,2,3,4], help="1=Urgent, 2=High, 3=Normal, 4=Low")
                create_task_parser.add_argument("-s", "--status", type=str, default="Open",  help="The status of the task")

                try:
                    args = create_task_parser.parse_args(command_args)                    
                except SystemExit as e:
                    await message_obj.channel.send(f"Invalid arguments for create-task command: {e}")
                    return
                
                args.time *= 1000 # convert to milliseconds
                print(f"Creating task '{args.task_name}'")
                message = self.commandCreateTask(args)
                await message_obj.channel.send(message)
            
            case ["clickup-team"]:
                # $dexnet clickup-team
                with message_obj.channel.typing():
                    message = self.commandClickupTeam()
                    await message_obj.channel.send(message)
            
            case ["save-list", list_id]:
                # $dexnet save-list list_id
                print(f"Saving list '{list_id}'")
                await self.commandSaveClickUpList(list_id, message_obj)
            
            case ["list-lists"]:
                # $dexnet list-lists
                print("Listing clickup lists")
                await self.commandListClickUpLists(message_obj)    
            
            case ["list-team", list_id]:
                namespace = argparse.Namespace(list_id=list_id)
                print(f"Listing members for list '{list_id}'")
                message = self.commandGetListMemebers(namespace)
                await message_obj.channel.send(message)
            
            case ["task-assign", *task_assign_args] if len(task_assign_args) >= 2:
                # $dexnet task-assign task_id -a clickup_user_id...
                task_assign_parser = argparse.ArgumentParser(description="Assign a task to a user", usage="$dexnet task-assign task_id -a clickup_user_id...")
                task_assign_parser.add_argument("task_id", type=str, help="The id of the task to assign")
                task_assign_parser.add_argument("-a", "--assign", type=str, action="append", help="The id of the user to assign the task to")
                try:
                    print(task_assign_args)
                    task_assign_namespace = task_assign_parser.parse_args(task_assign_args)
                except SystemExit as e:
                    await message_obj.channel.send(f"Invalid arguments for task-assign command: {e}")
                    return

                message = self.commandAssignTask(task_assign_namespace)
                await message_obj.channel.send(message)

            case ["admin-help"]:
                await message_obj.channel.send(f"```sql\n{self.AdminHelp}```")
            case other:
                print(f"Unknown command '{command}'")
                await message_obj.reply(f"Unknown command '{other}'")
    
    @property
    def AdminHelp(self) -> str:
        help_msg = f"{self.CommandPrefix}help - List all commands\n"
        help_msg += f'''
        \t{self.CommandPrefix}create-project project_name clickup_list_id github_repo - Create a new project
        \t{self.CommandPrefix}new-feature project_name issue_title issue_body - Create a new both as a task clickup and a github issue
        \t{self.CommandPrefix}new-dev project_name github_username - Add a new developer to a project
        \t{self.CommandPrefix}set-assignee project_name issue_id github_user - Set the assignee for an issue on github
        \t{self.CommandPrefix}list-devs project_name - List all developers for a project
        \t{self.CommandPrefix}list-issues project_name - List all github issues for a project
        \t{self.CommandPrefix}create-issue project_name issue_title issue_body - Create a new github issue and links it to a clickup task
        \t{self.CommandPrefix}create-task list_id task_name task_description - Create a new task on a clickup list
        \t{self.CommandPrefix}save-list list_id - Save a clickup list to the database
        \t{self.CommandPrefix}list-lists - List all clickup lists
        \t{self.CommandPrefix}list-members list_id - List all members of a clickup list
        \t{self.CommandPrefix}admin-help - List all admin commands, only visible to admins
        \t{self.CommandPrefix}task-assign task_id -a clickup_user_id... - Assign a task to a user on clickup
        '''    
        
        return help_msg
    
    @property
    def Servers(self) -> list:
        return self.config["servers_data"]
    
    def saveConfig(self) -> None:
        config_file = path.join(self.data_path, "config.json")
        with open(config_file, "w") as f:
            json.dump(self.config, f, indent=4)
        return
    
    def setAssignee(self, project_name:str, issue:int, github_user:str) -> bool:
        if project_name not in self.projects:
            return False
        
        github_repo = self.projects[project_name].github_repo_name
        github_url = f"https://api.github.com/repos/{self.GitHubUser}/{github_repo}/issues/{issue}/assignees"
        print(f"Github url: {github_url}")
        
        session = requests.session()
        session.auth = (self.GitHubUser, self.GitHubToken)
        
        issue_data = {
            "assignees": [github_user]
        }
        
        response = session.post(github_url, json=issue_data)
        session.close()
        print(f"Response: {response.content}")
        return response.status_code < 300 
        
    def saveProjects(self) -> None:
        projects_file = path.join(self.data_path, "projects.json")
        projects_data = {}
        for project_name, project_obj in self.projects.items():
            projects_data[project_name] = project_obj.asdict()
        
        with open(projects_file, "w") as f:
            json.dump(projects_data, f, indent=4)
        return
    
    def verifyGithubUser(self, github_user:str) -> bool:
        session = requests.session()
        session.auth = (self.GitHubUser, self.GitHubToken)
        
        github_user_url = f"https://api.github.com/users/{github_user}"
        response = session.get(github_user_url)
        session.close()
        
        return response.status_code == 200
        
            
            
            
            
            
            
            
            
            
            
            
            
            
            
            
            
            
            
            
            