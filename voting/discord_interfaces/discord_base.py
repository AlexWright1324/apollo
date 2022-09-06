import discord
from typing import List, Tuple, NamedTuple, Iterable, Dict

from discord.ui import View, Button
from sqlalchemy.exc import SQLAlchemyError

from models import db_session, User
from models.votes import DiscordVote, DiscordVoteChoice, DiscordVoteMessage, VoteType, UserVote
from utils import get_database_user_from_id
from voting.emoji_list import default_emojis

from voting.vote_types.base_vote import base_vote
from discord import AllowedMentions, InteractionMessage
from discord.ext.commands import Context

DENSE_ARRANGE = True
Choice = NamedTuple("Choice", [('emoji', str), ('prompt', str)])
DCMessage = NamedTuple("DCMessage", [('msg', discord.Message), ('choices', List[Choice])])

# Records last ephemeral message to each user, so can edit for future votes
users_last_vote_update_message: Dict[Tuple[int, int], InteractionMessage] = {}
class VoteButton(Button):
    def __init__(self, dvc: DiscordVoteChoice, msg_title):
        super().__init__(label=dvc.choice.choice, emoji=dvc.emoji)
        self.dvc = dvc
        self.vote = dvc.msg.vote
        self.msg_title = msg_title


    async def callback(self, interaction: discord.Interaction):
        db_user = db_session.query(User).filter(User.user_uid == interaction.user.id).one_or_none()

        if existing_vote := await self.get_existing_vote(interaction, db_user):
            msg = await self.remove_action(interaction, db_user, existing_vote)
        else:
            msg = await self.add_action(interaction, db_user)
        await self.send_feedback(interaction, db_user, msg)


    async def get_existing_vote(self, interaction: discord.Interaction, db_user):
        print(db_session.query(UserVote).filter(
            UserVote.vote_id == self.vote.id and
            UserVote.user_uid == db_user.id and
            UserVote.choice == self.dvc.choice.choice_index
        ).all())
        return db_session.query(UserVote).filter(
            UserVote.vote_id == self.vote.id and
            UserVote.user_uid == db_user.id and
            UserVote.choice == self.dvc.choice.choice_index
        ).one_or_none()

    async def add_action(self, interaction: discord.Interaction, db_user):
        user_vote = UserVote(vote_id=self.vote.id, user_id=db_user.id, choice=self.dvc.choice.choice_index)
        db_session.add(user_vote)
        db_session.commit()
        return f"Added Vote for {self.dvc.choice.choice}, {self.dvc.choice_index}"

    async def remove_action(self, interaction: discord.Interaction, db_user, existing_vote):
        db_session.delete(existing_vote)
        db_session.commit()
        return f"Removed Vote for {self.dvc.choice.choice}, {self.dvc.choice_index}"

    async def send_feedback(self, interaction: discord.Interaction, db_user, msg):
        # Check if existing feedback message and attempt to send to it
        key = (db_user.id, self.vote.id)
        if old_msg := users_last_vote_update_message.get(key):
            try:
                await old_msg.edit(content=msg)
                # Hack to give interaction a response without changing anything
                await interaction.response.edit_message(content=f"**{self.interface.get_title(self.vote.title)}**")
                return
            except (discord.errors.NotFound, discord.errors.HTTPException):
                pass
        # If no existing message, send it and update record for user
        await interaction.response.send_message(msg, ephemeral=True)
        new_msg = await interaction.original_response()
        users_last_vote_update_message[key] = new_msg


class DiscordBase:
    def __init__(self, btn_class=VoteButton):
        self.vote_type = base_vote
        self.bot = None
        self.BtnClass = btn_class

    async def create_vote(self, ctx: Context, args: List[str], vote_limit=None, seats=None):
        title, emoji_choices = self.parse_choices(args)
        choices = [c.prompt for c in emoji_choices]

        try:
            # Create DB entry for vote
            # TODO Get DB user from DC user properly
            owner = get_database_user_from_id(ctx.author.id)  # Questionable
            vote_obj, choices_obj = self.vote_type.create_vote(title, owner.id, choices, VoteType.basic, vote_limit, seats)
            new_dc_vote = DiscordVote(vote=vote_obj)
            db_session.add(new_dc_vote)

            # Post messages
            messages: List[DCMessage] = []
            msg_index = 0
            for chunk in self.chunk_choices(emoji_choices):
                msg_title = self.get_title(title, msg_index)
                # Send msg
                embed = self.create_embed(title, chunk)
                msg = await ctx.send(content=msg_title, embed=embed, allowed_mentions=AllowedMentions.none())
                messages.append(DCMessage(msg, [c for i, c in chunk]))

                # Add msg to DB
                start_ind, _ = chunk[0]
                end_ind, _ = chunk[-1]
                end_ind += 1
                new_dc_msg = DiscordVoteMessage(message_id=msg.id, channel_id=msg.channel.id, vote=vote_obj,
                                                choices_start_index=start_ind, numb_choices=end_ind - start_ind, part=msg_index)
                db_session.add(new_dc_msg)
                msg_index += 1

                # Add choices to DB and add buttons
                view = View(timeout=None)
                for db_ch, (i, ch) in zip(choices_obj[start_ind:end_ind], chunk):
                    print("\t", db_ch, (i, ch))
                    if db_ch.choice_index != i: raise Exception(f"DB and bot disagree on choice index")
                    new_dc_choice = DiscordVoteChoice(choice=db_ch, emoji=ch.emoji, msg=new_dc_msg)
                    db_session.add(new_dc_choice)

                    view.add_item(self.BtnClass(new_dc_choice, msg_title))
                await msg.edit(view=view)

            db_session.commit()
        except SQLAlchemyError:
            db_session.rollback()
            await ctx.send("Error creating vote")
            raise


        await ctx.message.add_reaction("✅")


    def get_title(self, title, msg_index):
        if msg_index == 0: return f"**Basic Vote: {title}**"
        else: return f"**Basic Vote: {title} pt. {msg_index+1}**"

    def get_description(self): return "Votes: Visible"

    def parse_choices(self, args: List[str]) -> Tuple[str, List[Choice]]:
        """Parse title and choices out of args"""
        if len(args) > 256: raise Exception(f"More than 256 choices given")
        if len(args) == 0: raise Exception(f"No choices given")

        # Truncate each choice to 256 chars
        for i, c in enumerate(args):
            if len(c) > 250: args[i] = c[:250] + "..."

        # Title is first argument
        title = args[0]
        choices = args[1:]

        # Pair choices with emojis -- thumbs up/down if single option given
        if len(choices) <= 1:
            c = choices[0] if choices else ""
            return title, [Choice("👍", c), Choice("👎", c)]
        else:
            return title, [Choice(e, c) for e, c in zip(default_emojis, choices)]


    def chunk_choices(self, choices: List[Choice], per_msg=20, len_per_msg=5900) -> Iterable[List[Tuple[int, Choice]]]:
        """Splits options such that they'll fit onto a message. Each msg can have 20 reacts and each embed can have max 6000 chars for the whole thing"""
        chunk, msg_len = [], 0
        for i, choice in enumerate(choices):
            line_len = len(choice.emoji) + len(choice.prompt) + 4
            if len(chunk)+1 > per_msg or msg_len + line_len > len_per_msg:
                yield chunk
                chunk, msg_len = [], 0
            chunk.append((i, choice))
            msg_len += line_len
        if chunk: yield chunk

    def create_embed(self, title: str, chunk: List[Tuple[int, Choice]]):
        """Construct embed from list of choices"""
        embed = discord.Embed(title=self.get_description())
        for i, ch in chunk:
            if len(ch.prompt) > 250: ch.prompt = ch.prompt[:250]
            embed.add_field(name=ch.emoji + " " + ch.prompt, value="_ _",
                            inline=(DENSE_ARRANGE and len(ch.prompt) < 25))
        return embed


    async def record_vote(self, vote, user, option):
        raise NotImplemented()

    async def make_results(self, vote):
        raise NotImplemented()


discord_base = DiscordBase()
