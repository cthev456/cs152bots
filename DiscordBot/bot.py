# bot.py
import discord
from discord.ext import commands
import os
import json
import logging
import re
import requests
from report import State, Category, SpamType, Report
import pdb

# Set up logging to the console
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

# There should be a file called 'tokens.json' inside the same folder as this file
token_path = 'tokens.json'
if not os.path.isfile(token_path):
    raise Exception(f"{token_path} not found!")
with open(token_path) as f:
    # If you get an error here, it means your token is formatted incorrectly. Did you put it in quotes?
    tokens = json.load(f)
    discord_token = tokens['discord']


class ModBot(discord.Client):
    def __init__(self): 
        intents = discord.Intents.default()
        intents.message_content = True
        # intents.messages = True 
        super().__init__(command_prefix='.', intents=intents)
        self.group_num = None
        self.mod_channels = {} # Map from guild to the mod channel id for that guild
        self.reports = {}  # Map from user IDs to the state of their report
        self.responses = json.load(open("response.json"))

    async def on_ready(self):
        print(f'{self.user.name} has connected to Discord! It is these guilds:')
        for guild in self.guilds:
            print(f' - {guild.name}')
        print('Press Ctrl-C to quit.')

        # Parse the group number out of the bot's name
        match = re.search('[gG]roup (\d+) [bB]ot', self.user.name)
        if match:
            self.group_num = match.group(1)
        else:
            raise Exception("Group number not found in bot's name. Name format should be \"Group # Bot\".")

        # Find the mod channel in each guild that this bot should report to
        for guild in self.guilds:
            for channel in guild.text_channels:
                if channel.name == f'group-{self.group_num}-mod':
                    self.mod_channels[guild.id] = channel
        

    async def on_message(self, message):
        '''
        This function is called whenever a message is sent in a channel that the bot can see (including DMs). 
        Currently the bot is configured to only handle messages that are sent over DMs or in your group's "group-#" channel. 
        '''
        # Ignore messages from the bot 
        if message.author.id == self.user.id:
            return

        # Check if this message was sent in a server ("guild") or if it's a DM
        if message.guild:
            await self.handle_channel_message(message)
        else:
            await self.handle_dm(message)

    async def handle_dm(self, message):
        # Handle a help message
        if message.content == Report.HELP_KEYWORD:
            reply =  "Use the `report` command to begin the reporting process.\n"
            reply += "Use the `cancel` command to cancel the report process.\n"
            await message.channel.send(reply)
            return

        author_id = message.author.id
        responses = []

        # Only respond to messages if they're part of a reporting flow
        if author_id not in self.reports and not message.content.startswith(Report.START_KEYWORD):
            return

        # If we don't currently have an active report for this user, add one
        if author_id not in self.reports:
            self.reports[author_id] = Report(self)

        # Let the report class handle this message; forward all the messages it returns to us
        responses = await self.reports[author_id].handle_message(message)
        for r in responses:
            await message.channel.send(r)

        # violation detection
        if self.reports[author_id].state == State.REPORT_COMPLETE and message.content != self.reports[author_id].CANCEL_KEYWORD:
            # None spam report, detect and reply
            if self.reports[author_id].spam_type is None:
                # no violation
                eval_result = self.eval_text(self.reports[author_id].message.content)
                if eval_result == "unidentified" or self.reports[author_id].report_type not in eval_result:
                    mod_message = "No violation corresponding to reported type"
                else:
                    mod_message = f"Found violation: {eval_result}."
                mod_message = "[Report Result]: " + mod_message
                await message.channel.send(mod_message)
                self.reports[author_id].state = State.MOD_COMPLETE
            else:
                eval_result = self.eval_text(self.reports[author_id].message.content)
                mod_message_to_reporter = None
                mod_message_to_reported = None
                if "spam" not in eval_result:
                    mod_message_to_reporter = self.responses["no_violation"]
                else:  # must be spam
                    if "serious" in eval_result:
                        mod_message_to_reported = self.responses["perm_suspend"]
                        # also delete reported message
                        await self.reports[author_id].message.delete()
                if mod_message_to_reporter is not None:
                    mod_message_to_reporter = "[Report Result]: " + mod_message_to_reporter
                    await message.channel.send(mod_message_to_reporter)
                if mod_message_to_reported is not None:
                    mod_message_to_reported = "[Report Result]: " + mod_message_to_reported
                    # Re-finding the user instead of just using the user from the message object
                    # here is a little unclean, but is neccesary in case we've deleted the message
                    member = await self.fetch_user(self.reports[author_id].reported_author_id)
                    channel = await member.create_dm()
                    await channel.send(mod_message_to_reported)
                self.reports[author_id].state = State.MOD_COMPLETE

        # If the report is complete or cancelled, remove it from our map
        if self.reports[author_id].mod_complete():
            # message to reporter
            await message.channel.send(self.responses["report_complete"])
            self.reports.pop(author_id)

    async def handle_channel_message(self, message):
        # Only handle messages sent in the "group-#" channel
        if not message.channel.name == f'group-{self.group_num}':
            return

        # Forward the message to the mod channel
        mod_channel = self.mod_channels[message.guild.id]
        await mod_channel.send(f'Forwarded message:\n{message.author.name}: "{message.content}"')
        scores = self.eval_text(message.content)
        await mod_channel.send(self.code_format(scores))

    
    def eval_text(self, message):
        ''''
        TODO: Once you know how you want to evaluate messages in your channel, 
        insert your code here! This will primarily be used in Milestone 3. 
        '''
        result = ""
        if Category.SPAM in message:
            if SpamType.ADVERTISING in message:
                result += "spam_advertising"
            elif SpamType.INVITES in message:
                result += "spam_invites"
            elif SpamType.MALICIOUS_LINKS in message:
                result += "spam_links"
            elif SpamType.OTHER in message:
                result += "spam_other"
            result += "spam"
        elif Category.VIOLENT in message:
            result += "violent"
        elif Category.HARASSMENT in message:
            result += "harassment"
        elif Category.NSFW in message:
            result += "nsfw"
        elif Category.HATE_SPEECH in message:
            result += "hate speech"
        elif Category.OTHER in message:
            result += "other"
        if "serious" in message:
            result += "_serious"
        else:
            result += "_minor"
        if result != "":
            return result
        else:
            return "unidentified"

    
    def code_format(self, text):
        ''''
        TODO: Once you know how you want to show that a message has been 
        evaluated, insert your code here for formatting the string to be 
        shown in the mod channel. 
        '''
        return "Evaluated: '" + text+ "'"


client = ModBot()
client.run(discord_token)