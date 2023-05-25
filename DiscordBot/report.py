from enum import Enum, auto
import discord
import re

class State(Enum):
    REPORT_START = auto()
    AWAITING_MESSAGE = auto()
    MESSAGE_IDENTIFIED = auto()
    OFFENDER_STATUS_IDENTIFIED = auto()
    AWAITING_SPAM_TYPE = auto()
    RECEIVED_SPAM_TYPE = auto()
    REPORT_COMPLETE = auto()
    MOD_COMPLETE = auto()

class Category:
    SPAM = 'spam'
    VIOLENT = 'violent'
    HARASSMENT = 'harassment'
    NSFW = 'nsfw'
    HATE_SPEECH = 'hate speech'
    OTHER = 'other'

class SpamType:
    ADVERTISING = 'advertising'
    INVITES = 'invites'
    MALICIOUS_LINKS = 'links'
    OTHER = 'other'
        
class Report:
    START_KEYWORD = "report"
    CANCEL_KEYWORD = "cancel"
    HELP_KEYWORD = "help"

    def __init__(self, client):
        self.state = State.REPORT_START
        self.client = client
        self.channel = None
        self.message = None
        self.report_type = None
        self.repeat_offender = None
        self.spam_type = None
        self.block_user = None
        
    async def handle_message(self, message):
        '''
        This function makes up the meat of the user-side reporting flow. It defines how we transition between states and what 
        prompts to offer at each of those states. You're welcome to change anything you want; this skeleton is just here to
        get you started and give you a model for working with Discord. 
        '''

        if message.content == self.CANCEL_KEYWORD:
            self.state = State.MOD_COMPLETE
            return ["Report cancelled."]
        
        if self.state == State.REPORT_START:
            reply =  "Thank you for starting the reporting process. "
            reply += "Say `help` at any time for more information.\n\n"
            reply += "Please copy paste the link to the message you want to report.\n"
            reply += "You can obtain this link by right-clicking the message and clicking `Copy Message Link`."
            self.state = State.AWAITING_MESSAGE
            return [reply]
        
        if self.state == State.AWAITING_MESSAGE:
            # Parse out the three ID strings from the message link
            m = re.search('/(\d+)/(\d+)/(\d+)', message.content)
            if not m:
                return ["I'm sorry, I couldn't read that link. Please try again or say `cancel` to cancel."]
            guild = self.client.get_guild(int(m.group(1)))
            if not guild:
                return ["I cannot accept reports of messages from guilds that I'm not in. Please have the guild owner add me to the guild and try again."]
            channel = guild.get_channel(int(m.group(2)))
            if not channel:
                return ["It seems this channel was deleted or never existed. Please try again or say `cancel` to cancel."]
            try:
                message = await channel.fetch_message(int(m.group(3)))
            except discord.errors.NotFound:
                return ["It seems this message was deleted or never existed. Please try again or say `cancel` to cancel."]

            # Here we've found the message - it's up to you to decide what to do next!
            self.state = State.MESSAGE_IDENTIFIED
            self.message = message
            self.channel = channel
            report_reply = "I found this message:" + "```" + message.author.name + ": " + message.content + "``` \n" + "Please reply with the options that closely match the reason for your report: \n"
            report_reply += "1. Spam; type 'spam' \n"
            report_reply += "2. Violent Content; type 'violent' \n"
            report_reply += "3. Bullying or Harassment; type 'harassment' \n"
            report_reply += "4. NSFW Content; type 'nsfw' \n"
            report_reply += "5. Hate Speech; type 'hate speech' \n"
            report_reply += "6. Other; type 'other' \n"
            return [report_reply]
    
        if self.state == State.MESSAGE_IDENTIFIED:
            if message.content not in [Category.SPAM, Category.VIOLENT, Category.HARASSMENT, Category.NSFW, Category.HATE_SPEECH, Category.OTHER]:
                report_reply = "I'm sorry, I didn't understand that. Please reply with the options that closely match the reason for your report : \n"
                report_reply += "1. Spam; type 'spam' \n"
                report_reply += "2. Violent Content; type 'violent' \n"
                report_reply += "3. Bullying or Harassment; type 'harassment' \n"
                report_reply += "4. NSFW Content; type 'nsfw' \n"
                report_reply += "5. Hate Speech; type 'hate speech' \n"
                report_reply += "6. Other; type 'other' \n"
                return [report_reply]
            if message.content != Category.SPAM:
                self.report_type = message.content
                self.state = State.REPORT_COMPLETE
                return ["Thank you for your report. I have forwarded it to the moderators of this server for immediate action. Any content that violates the Discord Terms of Service or this server's rules will be removed. The reported user will also be banned temporarily or permanently. We thank you for making this server a safe place!\n"]
            else: # spam
                self.state = State.OFFENDER_STATUS_IDENTIFIED
                report_reply = "Is this a repeat offender? \n Reply with 'yes' or 'no.' \n"
                return [report_reply]
        
        if self.state == State.OFFENDER_STATUS_IDENTIFIED:
            report_reply = ''
            if message.content not in ['yes', 'no']:
                report_reply += "I'm sorry, I didn't understand that. Is this a repeat offender? \n Reply with 'yes' or 'no.' \n"
                return [report_reply]
            if message.content == 'yes':
                self.repeat_offender = True
                report_reply += "I have noted that this is a repeat offender. \n"
            else:
                self.repeat_offender = False
            self.state = State.AWAITING_SPAM_TYPE
            return [report_reply + "Please reply with the options that closely match the type of spam present in the message: \n"
                    "1. The message is an unwanted advertisement that has nothing to do with the server; type 'advertising' \n"
                    "2. Unwanted invites to other servers; type 'invites' \n"
                    "3. The message contains a suspicious, abusive, or NSFW link; type 'links' \n"
                    "4. Other; type 'other' \n"]

        if self.state == State.AWAITING_SPAM_TYPE:
            if message.content not in [SpamType.ADVERTISING, SpamType.INVITES, SpamType.MALICIOUS_LINKS, SpamType.OTHER]:
                report_reply = "I'm sorry, I didn't understand that. Please reply with the options that closely match the type of spam present in the message: \n"
                report_reply += "1. The message is an unwanted advertisement that has nothing to do with the server; type 'advertising' \n"
                report_reply += "2. Unwanted invites to other servers; type 'invites' \n"
                report_reply += "3. The message contains a suspicious, abusive, or NSFW link; type 'links' \n"
                report_reply += "4. Other; type 'other' \n"
                return [report_reply]
            self.spam_type = message.content
            self.state = State.RECEIVED_SPAM_TYPE
            return ["I have noted that the spam type is " + self.spam_type + ". Would you like to block this user and any future accounts they make? Reply with 'yes' or 'no.' \n"]
        
        if self.state == State.RECEIVED_SPAM_TYPE:
            report_reply = ''
            if message.content not in ['yes', 'no']:
                report_reply += "I'm sorry, I didn't understand that. Would you like to block this user and any future accounts they make? Reply with 'yes' or 'no.' \n"
                return [report_reply]
            if message.content == 'yes':
                self.block_user = True
                report_reply += "I have noted that you would like to block this user and any future accounts they make. \n"
            self.block_user = False
            self.state = State.REPORT_COMPLETE
            return ["Thank you for your report. I have forwarded it to the moderators of this server for immediate action. Any content that violates the Discord Terms of Service or this servers rules will be removed. The reported user will also be banned temporarily or permanently. We thank you for making this server a safe place!\n"]
        return []

    def report_complete(self):
        return self.state == State.REPORT_COMPLETE   


    def mod_complete(self):
        return self.state == State.MOD_COMPLETE                    


        

