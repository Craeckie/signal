# mautrix-signal - A Matrix-Signal puppeting bridge
# Copyright (C) 2021 Tulir Asokan
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
from __future__ import annotations

from typing import cast
import html
import struct

from mausignald.types import Address, Mention, MessageData
from mautrix.types import Format, MessageType, TextMessageEventContent, UserID
from mautrix.util.formatter import (
    EntityString,
    EntityType,
    MarkdownString,
    MatrixParser as BaseMatrixParser,
    SemiAbstractEntity,
)

from . import puppet as pu, user as u


# Helper methods from from https://github.com/LonamiWebs/Telethon/blob/master/telethon/helpers.py
# I don't know if this is how Signal actually calculates lengths,
# but it seems to work better than plain len()
def add_surrogate(text: str) -> str:
    return "".join(
        "".join(chr(y) for y in struct.unpack("<HH", x.encode("utf-16le")))
        if (0x10000 <= ord(x) <= 0x10FFFF)
        else x
        for x in text
    )


def del_surrogate(text: str) -> str:
    return text.encode("utf-16", "surrogatepass").decode("utf-16")


async def signal_to_matrix(message: MessageData) -> TextMessageEventContent:
    content = TextMessageEventContent(msgtype=MessageType.TEXT, body=message.body)
    surrogated_text = add_surrogate(message.body)
    if message.mentions:
        text_chunks = []
        html_chunks = []
        last_offset = 0
        for mention in message.mentions:
            before = surrogated_text[last_offset : mention.start]
            last_offset = mention.start + mention.length

            text_chunks.append(before)
            html_chunks.append(html.escape(before))
            puppet = await pu.Puppet.get_by_address(Address(uuid=mention.uuid))
            name = add_surrogate(puppet.name or puppet.mxid)
            text_chunks.append(name)
            html_chunks.append(f'<a href="https://matrix.to/#/{puppet.mxid}">{name}</a>')
        end = surrogated_text[last_offset:]
        text_chunks.append(end)
        html_chunks.append(html.escape(end))
        content.body = del_surrogate("".join(text_chunks))
        content.format = Format.HTML
        content.formatted_body = del_surrogate("".join(html_chunks))
    return content


class MentionEntity(Mention, SemiAbstractEntity):
    @property
    def offset(self) -> int:
        return self.start

    @offset.setter
    def offset(self, val: int) -> None:
        self.start = val

    def copy(self) -> MentionEntity:
        return MentionEntity(uuid=self.uuid, length=self.length, start=self.start)


# TODO this has a lot of duplication with mautrix-facebook, maybe move to mautrix-python
class SignalFormatString(EntityString[MentionEntity, EntityType], MarkdownString):
    def format(self, entity_type: EntityType, **kwargs) -> SignalFormatString:
        prefix = suffix = ""
        if entity_type == EntityType.USER_MENTION:
            self.entities.append(
                MentionEntity(uuid=kwargs["uuid"], start=0, length=len(self.text)),
            )
            return self
        elif entity_type == EntityType.BOLD:
            prefix = suffix = "**"
        elif entity_type == EntityType.ITALIC:
            prefix = suffix = "_"
        elif entity_type == EntityType.STRIKETHROUGH:
            prefix = suffix = "~~"
        elif entity_type == EntityType.URL:
            if kwargs["url"] != self.text:
                suffix = f" ({kwargs['url']})"
        elif entity_type == EntityType.PREFORMATTED:
            prefix = f"```{kwargs['language']}\n"
            suffix = "\n```"
        elif entity_type == EntityType.INLINE_CODE:
            prefix = suffix = "`"
        elif entity_type == EntityType.BLOCKQUOTE:
            children = self.trim().split("\n")
            children = [child.prepend("> ") for child in children]
            return self.join(children, "\n")
        elif entity_type == EntityType.HEADER:
            prefix = "#" * kwargs["size"] + " "
        else:
            return self

        self._offset_entities(len(prefix))
        self.text = f"{prefix}{self.text}{suffix}"
        return self


class MatrixParser(BaseMatrixParser[SignalFormatString]):
    fs = SignalFormatString

    async def user_pill_to_fstring(
        self, msg: SignalFormatString, user_id: UserID
    ) -> SignalFormatString:
        user = await u.User.get_by_mxid(user_id, create=False)
        if user and user.uuid:
            uuid = user.uuid
        else:
            puppet = await pu.Puppet.get_by_mxid(user_id, create=False)
            if puppet:
                uuid = puppet.uuid
            else:
                return msg
        return msg.format(self.e.USER_MENTION, uuid=uuid)

    async def parse(self, data: str) -> SignalFormatString:
        return cast(SignalFormatString, await super().parse(data))


async def matrix_to_signal(content: TextMessageEventContent) -> tuple[str, list[Mention]]:
    if content.msgtype == MessageType.EMOTE:
        content.body = f"/me {content.body}"
        if content.formatted_body:
            content.formatted_body = f"/me {content.formatted_body}"
    if content.format == Format.HTML and content.formatted_body:
        parsed = await MatrixParser().parse(add_surrogate(content.formatted_body))
        text, mentions = del_surrogate(parsed.text), parsed.entities
    else:
        text, mentions = content.body, []
    return text, mentions
