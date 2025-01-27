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
import os
import openai
import time

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
    openai_token = tokens['openai']
    openai_org = tokens['openai_org']


openai.organization = openai_org
openai.api_key = openai_token


class ModBot(discord.Client):
    def __init__(self): 
        intents = discord.Intents.default()
        intents.message_content = True
        # intents.messages = True 
        super().__init__(command_prefix='.', intents=intents)
        self.group_num = None
        self.mod_channels = {} # Map from guild to the mod channel id for that guild
        self.reports = {}  # Map from user IDs to the state of their report
        self.moderation_actions = {}
        self.responses = json.load(open("response.json"))
        self.report_history = json.load(open("report_history.json"))
        self.next_report_id = 0

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
            if "mod" in message.channel.name:
                await self.handle_mod_channel_message(message)
            else:
                await self.handle_channel_message(message)
        else:
            await self.handle_dm(message)


    async def handle_moderation(self, report, eval_result):
        if "second" in eval_result:
            print("[log]: requesting second opinion")
            report.priority_score += 10.0
            report.state = State.AWAITING_SECOND_MOD
            return
        
        reported_id = report.message.author.id
        if report.spam_type is None:
            # no violation
            print("[log] ", eval_result)
            if eval_result == "unidentified": # or report.report_type not in eval_result:
                mod_message = "No violation corresponding to reported type"
                print("[log] No violation")
            else:
                mod_message = f"Found violation: {eval_result}."
                self.report_history[reported_id][1] += 1
                print(f"[log] found violation {eval_result}")
            mod_message = "[Report Result]: " + mod_message
            await report.reporter_channel.send(mod_message)
            report.state = State.MOD_COMPLETE
        else:
            mod_message_to_reporter = None
            mod_message_to_reported = None
            if "spam" not in eval_result:
                mod_message_to_reporter = self.responses["no_violation"]
                print("[log] no spam violation")
            else:  
                self.report_history[reported_id][1] += 1
                remove_public_post = False
                n_violation = self.report_history[reported_id][1]
                print(f"[log] times confirmed: {n_violation}")
                if n_violation >= 3 or "permban" in eval_result:
                    mod_message_to_reported = self.responses["perm_suspend"]
                    print("[log] perm suspend")
                elif n_violation >= 2:
                    mod_message_to_reported = self.responses["1week_suspend"]
                    print("[log] 1 week suspend")
                elif n_violation == 1:
                    mod_message_to_reported = self.responses["24hr_suspend"]
                    print("[log] 24hr suspend")
                else:
                    remove_public_post = False

                if remove_public_post:
                    # delete reported message
                    await report.message.delete()
                    print("[log] remove message")
                            
            if mod_message_to_reporter is not None:
                mod_message_to_reporter = "[Report Result]: " + mod_message_to_reporter
                await report.reporter_channel.send(mod_message_to_reporter)
            if mod_message_to_reported is not None:
                mod_message_to_reported = "[Report Result]: " + mod_message_to_reported
                # Re-finding the user instead of just using the user from the message object
                # here is a little unclean, but is neccesary in case we've deleted the message
                member = await self.fetch_user(report.reported_author_id)
                channel = await member.create_dm()
                await channel.send(mod_message_to_reported)
            report.state = State.MOD_COMPLETE
        # record report history
        with open("report_history.json", "w") as f:
            json.dump(self.report_history, f)

    async def handle_mod_flow(self, message):
        author_id = message.author.id
        mod_channel = list(self.mod_channels.values())[0]


        if author_id not in self.moderation_actions or message.content == "moderate":
            # show list of reports sorted by order
            sorted_reports = [report for report in self.reports.values()]
            sorted_reports.sort(reverse=True, key=lambda x: x.priority_score)
            sorted_reports = [(report.id, report.priority_score) for report in sorted_reports]
            await mod_channel.send(f"List of reports sorted by priority: {sorted_reports}")
            self.moderation_actions[author_id] = None
            await mod_channel.send('Please say the id of the report to moderate')
            return

        if not self.moderation_actions[author_id]:
            await mod_channel.send('Thank you. Finding that report now')
            report_id = int(message.content)
            for report in self.reports.values():
                if report.id == report_id:
                    if report.state == State.AWAITING_SECOND_MOD:
                        self.moderation_actions[author_id] = report
                        if report.state == State.AWAITING_SECOND_MOD:
                            report.state = State.AWAITING_SECOND_MOD_CONFIRM
                            await mod_channel.send(f"Do you agree with the first moderator's judgement: {report.eval_type}? type 'yes' or 'no'")
                            return
                    if report.state == State.AWAITING_MOD:
                        self.moderation_actions[author_id] = report
                        await mod_channel.send('I found the report with this message:' + "```" + report.message.author.name + ": " + report.message.content + "``` \n")
                        # report.eval_type = self.eval_text(report.message.content)
                        await mod_channel.send(f'The autoclassifier thinks this is a violation of type {report.eval_type}. Is this correct? type "yes" or "no"')   
                        report.state = State.AWAITING_MOD_CONFIRM
                        return
                    else:
                        await mod_channel.send('It appears someone else is already moderating this message.')
                    break

        report = self.moderation_actions[author_id]

        

        if report.state == State.AWAITING_SECOND_MOD_CONFIRM:
            if message.content not in ['yes', 'no']:
                await mod_channel.send("I'm sorry, I didn't understand that. Reply with 'yes' or 'no.' \n")
                return
            if message.content == "yes":
                report.eval_type = report.eval_type.replace("second", "permban")
                await mod_channel.send("Thank you. Finalizing evaluation.")  # original 
                print(report.eval_type)  # original 
                await self.handle_moderation(report, report.eval_type)
            else:
                report.eval_type = "unidentified"
                await mod_channel.send("Thank you. Finalizing evaluation.")
                await self.handle_moderation(report, report.eval_type)


        if report.state == State.AWAITING_MOD_CONFIRM:
            report_reply = ''
            if message.content not in ['yes', 'no']:
                await mod_channel.send("I'm sorry, I didn't understand that. Is the classification given correct? \n Reply with 'yes' or 'no.' \n")
                return
            if message.content == 'yes':
                if 'serious' in report.eval_type:
                    report.eval_type += "_second"
                    await mod_channel.send("Second moderator opinion requested. Thank you. Finalizing evaluation.")
                await mod_channel.send("Thank you. Finalizing evaluation.")  
                await self.handle_moderation(report, report.eval_type)
                return
            else:
                report.state = State.AWAITING_MOD_CLASSIFICATION
                response = 'Ok. What type of violation is this? Please reply with one of:\n'
                response += "1. Spam; type 'spam' \n"
                response += "2. Violent Content; type 'violent' \n"
                response += "3. Bullying or Harassment; type 'harassment' \n"
                response += "4. NSFW Content; type 'nsfw' \n"
                response += "5. Hate Speech; type 'hate speech' \n"
                response += "6. Other; type 'other' \n"
                response += "7. None; type 'unidentified'"
                await mod_channel.send(response)
                return

        if report.state == State.AWAITING_MOD_CLASSIFICATION:
            if message.content not in [Category.SPAM, Category.VIOLENT, Category.HARASSMENT, Category.NSFW, Category.HATE_SPEECH, Category.OTHER, 'unidentified']:
                report_reply = "I'm sorry, I didn't understand that. Please reply with one of: \n"
                report_reply += "1. Spam; type 'spam' \n"
                report_reply += "2. Violent Content; type 'violent' \n"
                report_reply += "3. Bullying or Harassment; type 'harassment' \n"
                report_reply += "4. NSFW Content; type 'nsfw' \n"
                report_reply += "5. Hate Speech; type 'hate speech' \n"
                report_reply += "6. Other; type 'other' \n"
                report_reply += "7. None; type 'unidentified'"
                await mod_channel.send(report_reply)
                return
            if message.content == Category.SPAM:  # spam
                report.eval_type += '_' + message.content
                report.state = State.AWAITING_MOD_SUBCLASSIFICATION
                report_reply = "Please reply with the options that closely match the type of spam present in the message: \n" \
                    + "1. The message contains external link. type 'links' \n" \
                    + "2. The message is an unwanted advertisement that has nothing to do with the server. type 'advertising' \n" \
                    + "3. The message contains personal or financial information. type 'personal' \n" \
                    + "4. Unwanted invites to other servers; type 'invites' \n" \
                    + "5. Trolls/harassment; type 'troll' \n" \
                    + "6. Human-like activities; type 'human' \n"
                await mod_channel.send(report_reply)
                return
            else:
                if message.content == 'unidentified':
                    report.eval_type = 'unidentified'
                    report.state = State.MOD_COMPLETE
                    await mod_channel.send("Thank you")
                    return
                response = "Is the violation minor or severe?"
                report.eval_type = 'violation_' + message.content
                report.state = State.AWAITING_MOD_SEVERITY
                await mod_channel.send(response)
                return

        if report.state == State.AWAITING_MOD_SUBCLASSIFICATION:
            if message.content not in [SpamType.LINKS, SpamType.PERSONAL, SpamType.TROLL, SpamType.HUMAN, SpamType.ADVERTISING, SpamType.INVITES]:
                report_reply = "I'm sorry, I didn't understand that. Please reply with the options that closely match the type of spam present in the message: \n" \
                    + "1. The message contains external link. type 'links' \n" \
                    + "2. The message is an unwanted advertisement that has nothing to do with the server. type 'advertising' \n" \
                    + "3. The message contains personal or financial information. type 'personal' \n" \
                    + "4. Unwanted invites to other servers; type 'invites' \n" \
                    + "5. Trolls/harassment; type 'troll' \n" \
                    + "6. Human-like activities; type 'human' \n"
                await mod_channel.send(report_reply)
                return
            report.eval_type += '_' + message.content

            report.spam_type = message.content

            if message.content in [SpamType.ADVERTISING, SpamType.PERSONAL, SpamType.INVITES]:
                report.state = State.AWAITING_MOD_LINK_INVOLVE
                await mod_channel.send("Is there a link in the message? type 'yes' or 'no'")
                return
            elif message.content in [SpamType.LINKS]:
                report.state = State.AWAITING_MOD_LINK_LEGIT
                await mod_channel.send("Is the link legitimate? type 'yes' or 'no'")
                return
            elif message.content in [SpamType.TROLL, SpamType.HUMAN]:
                report.state = State.AWAITING_MOD_MINOR_SPAM
                await mod_channel.send("Is this a minor spam violation? type 'yes' or 'no'")
                return
        
            # report.state = State.AWAITING_MOD_SEVERITY
            # response = "Is the violation minor or severe?"
            # await mod_channel.send(response)
            # return

        if report.state == State.AWAITING_MOD_LINK_INVOLVE:
            if message.content not in ['yes', 'no']:
                await mod_channel.send("Please type 'yes' or 'no'")
                return
            if message.content == "yes":
                report.state = State.AWAITING_MOD_LINK_LEGIT
                await mod_channel.send("Is the link legitimate? type 'yes' or 'no'")
                return
            else:
                report.state = State.AWAITING_MOD_LINK_SERIOIUS
                await mod_channel.send("Is this a serioius spam? type 'yes' or 'no'")
                return
                report.eval_type = "unidentified"
                await mod_channel.send("Thank you. Finalizing evaluation.")
                await self.handle_moderation(report, report.eval_type)
        
        if report.state == State.AWAITING_MOD_LINK_LEGIT:
            if message.content not in ['yes', 'no']:
                await mod_channel.send("Please type 'yes' or 'no'")
                return
            if message.content == "yes":
                report.state = State.AWAITING_MOD_LINK_SERIOIUS
                await mod_channel.send("Is this a serioius spam? type 'yes' or 'no'")
                return
            else:
                report.eval_type += "_second"
                await mod_channel.send("Second moderator opinion requested. Thank you. Finalizing evaluation.")
                await self.handle_moderation(report, report.eval_type)

        if report.state == State.AWAITING_MOD_LINK_SERIOIUS:
            if message.content not in ['yes', 'no']:
                await mod_channel.send("Please type 'yes' or 'no'")
                return
            if message.content == "yes":
                report.eval_type += "_second"
                await mod_channel.send("Second moderator opinion requested. Thank you. Finalizing evaluation.")
                await self.handle_moderation(report, report.eval_type)
            else:
                report.eval_type = "unidentified"
                await mod_channel.send("Thank you. Finalizing evaluation.")
                await self.handle_moderation(report, report.eval_type)

        if report.state == State.AWAITING_MOD_MINOR_SPAM:
            if message.content not in ['yes', 'no']:
                await mod_channel.send("Please type 'yes' or 'no'")
                return
            if message.content == "yes":
                await mod_channel.send("Thank you. Finalizing evaluation.")
                await self.handle_moderation(report, report.eval_type)
            else:
                report.eval_type = "unidentified"
                await mod_channel.send("Thank you. Finalizing evaluation.")
                await self.handle_moderation(report, report.eval_type)



        if report.state == State.AWAITING_MOD_SEVERITY:
            if message.content not in ['minor', 'severe']:
                report_reply = 'Please state either minor or severe'
                await mod_channel.send(report_reply)
                return
            report.eval_type += '_' + message.content
            await mod_channel.send("Thank you. Finalizing evaluation.")
            await self.handle_moderation(report, report.eval_type)

        # If the report is complete or cancelled, remove it from our map
        if self.moderation_actions[author_id].mod_complete():
            # message to reporter
            await self.moderation_actions[author_id].reporter_channel.send(self.responses["report_complete"])
            self.reports.pop(self.moderation_actions[author_id].reporter_author_id)
            self.moderation_actions.pop(author_id)

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
            self.reports[author_id].state = State.AWAITING_MOD
            # record report history for this user
            reported_id = self.reports[author_id].message.author.id
            if reported_id not in self.report_history:
                self.report_history[reported_id] = [0, 0] # reported, confirmed violation
            
            self.report_history[reported_id][0] += 1
            # None spam report, detect and reply
            self.reports[author_id].reporter_channel = message.channel
            self.reports[author_id].reporter_author_id = author_id

            self.reports[author_id].eval_type = self.eval_text(self.reports[author_id].message.content)

            # compute priority score
            auto_score = 0.0 if "violation" not in self.reports[author_id].eval_type else (
                1.0 if "serious" in self.reports[author_id].eval_type else 0.5
            )
            self.reports[author_id].priority_score = 1.0 * auto_score + 0.2 * self.report_history[reported_id][1] + 0.1 * self.report_history[reported_id][0]
            # self.reports[author_id].priority_score += (10.0 if "second" in self.reports[author_id].eval_type else 0.0)

            print("Auto score: ", auto_score)
            print("Confirmed: ", self.report_history[reported_id][1])
            print("reported: ", self.report_history[reported_id][0])
            print("Overall score: ", self.reports[author_id].priority_score)

            mod_channel = list(self.mod_channels.values())[0]
            await mod_channel.send(f'Report {self.reports[author_id].id} requires moderation')

        # record report history
        with open("report_history.json", "w") as f:
            json.dump(self.report_history, f)

    async def handle_channel_message(self, message):
        # Only handle messages sent in the "group-#" channel
        if not message.channel.name == f'group-{self.group_num}':
            return

        # Forward the message to the mod channel
        mod_channel = self.mod_channels[message.guild.id]
        await mod_channel.send(f'Forwarded message:\n{message.author.name}: "{message.content}"')
        scores = self.eval_text(message.content)
        await mod_channel.send(self.code_format(scores))

    async def handle_mod_channel_message(self, message):
        # Only handle messages sent in the "group-#" channel
        if not message.channel.name == f'group-{self.group_num}-mod':
            return

        # Forward the message to the mod channel
        mod_channel = self.mod_channels[message.guild.id]
        
        author_id = message.author.id
        if author_id in self.moderation_actions or message.content.startswith(Report.MOD_KEYWORD):
            await self.handle_mod_flow(message)
            return
    
    def eval_text(self, message):
        ''''
        TODO: Once you know how you want to evaluate messages in your channel, 
        insert your code here! This will primarily be used in Milestone 3. 
        '''
        retry = True
        retries = 0
        while retry and retries < 5:
            retries += 1
            retry = False
            try:
                response = openai.ChatCompletion.create(
                    model="gpt-4",# "gpt-3.5-turbo",
                    messages=[
                        {"role": "system", "content": "You are a content moderation system for online social media and SMS messages. Classify each message as flagged or not flagged."
                            "Messages should be flagged if they are spam, violent, harssment, not safe for work, hate speech, or otherwise violate content policy. Also classify a message as Other violation if the message contains the keyword 'cs152'."
                            "If the message is flagged, make a determination if it is a serious violation or non-serious violation. Messages with malicious intent or has potential negative impact on the receiver should be classified as serious."
                            "If it is spam, classify it as advertising, invites to other platforms, malicious links, or other."
                            "Additionally, if the message is spam, classify it as serious if it contains any link, Discord invite, or any form of advertising. Otherwise, it is non-serious spam."
                            "Your response should only contain keywords in [Flagged, Not flagged, Spam, Violent, Harrasment, Not safe for work, nsfw, Hate speech, Other, Non-serious, Serious, Advertising, Invites, Links"},
                        {"role": "user", "content": "Join my crypto discord server: https://discord.gg/XYBrZE8x."},
                        {"role": "assistant", "content": "Flagged. Spam. Non-serious. Invites."},
                        {"role": "user", "content": "We should play Call Of Duty Together."},
                        {"role": "assistant", "content": "Not flagged."},
                        {"role": "user", "content": "I'm going to kick your ass."},
                        {"role": "assistant", "content": "Flagged. Violent. Serious."},
                        {"role": "user", "content": "Free entry in 2 a wkly comp to win FA Cup final tkts 21st May 2005. Text FA to 87121 to receive entry question(std txt rate)T&C's apply 08452810075over18's"},
                        {"role": "assistant", "content": "Flagged. Spam. Serious. Advertising."},
                        {"role": "user", "content": "XXXMobileMovieClub: To use your credit, click the WAP link in the next txt message or click here>> http://wap. xxxmobilemovieclub.com?n=QJKGIGHJJGCBL"},
                        {"role": "assistant", "content": "Flagged. Spam. Serious. Links."},
                        {"role": "user", "content": message}
                    ]
                )

                output = response['choices'][0]['message']['content']

                print("GPT output: " + output)

                classifications_list = output.split('. ')
                if "not flagged" in classifications_list[0].lower() or len(classifications_list) < 2:
                    return "unidentified"

                classifications = output
                result = ""
                if "spam" in classifications.lower():
                    if SpamType.ADVERTISING in classifications.lower():
                        result += "violation_spam_advertising"
                    elif SpamType.INVITES in classifications.lower():
                        result += "violation_spam_invites"
                    elif SpamType.MALICIOUS_LINKS in classifications.lower():
                        result += "violation_spam_links"
                    elif SpamType.OTHER in classifications.lower():
                        result += "violation_spam_other"
                    else:
                        result += "violation_spam"
                elif "violent" in classifications.lower():
                    result += "violation_violent"
                elif "harassment" in classifications.lower():
                    result += "violation_harassment"
                elif "not safe for work" in classifications.lower() or "nsfw" in classifications.lower():
                    result += "violation_nsfw"
                elif "hate speech" in classifications.lower():
                    result += "violation_hate_speech"
                else:
                    result += "violation_other"

                if "non-serious" in classifications.lower():
                    result += "_minor"
                elif "serious" in classifications.lower():
                    result += "_serious"

                print(f"GPT classification: {result}")
                return result

            except (openai.error.APIError, openai.error.Timeout, openai.error.RateLimitError):
                retry = True
                print("Hit a recoverable OpenAI API error. Retrying in 1 second.")
                time.sleep(1)

            except (openai.error.APIConnectionError, 
                    openai.errors.InvalidRequestError, 
                    openai.errors.AuthenticationError, 
                    openai.errors.ServiceUnavailableError
                    ) as e:
                print(e)
                print("Hit unrecoverable OpenAI error. Falling back.")

        print("Activating fallback.")
        # TBH this should be better. Let's think about how to do this.
        result = ""
        if Category.SPAM in message:
            if SpamType.ADVERTISING in message:
                result += "violation_spam_advertising"
            elif SpamType.INVITES in message:
                result += "violation_spam_invites"
            elif SpamType.MALICIOUS_LINKS in message:
                result += "violation_spam_links"
            elif SpamType.OTHER in message:
                result += "violation_spam_other"
            else:
                result += "violation_spam"
        elif Category.VIOLENT in message:
            result += "violation_violent"
        elif Category.HARASSMENT in message:
            result += "violation_harassment"
        elif Category.NSFW in message:
            result += "violation_nsfw"
        elif Category.HATE_SPEECH in message:
            result += "violation_hate speech"
        elif Category.OTHER in message:
            result += "violation_other"
            
        if "serious" in message:
            result += "_serious"
        else:
            result += "_minor"
        if "violation" in result:
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

    def next_id(self):
        self.next_report_id += 1
        return self.next_report_id


client = ModBot()
client.run(discord_token)