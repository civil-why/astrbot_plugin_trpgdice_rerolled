"""
人物卡管理 Mixin。

提供 COC/D&D 人物卡的创建、显示、切换、更新、删除以及群名片同步。
@filter.command 装饰器不会被 AstrBot 调度，实际路由见 handler/router.py。
"""

import re
import random
from typing import Optional

from ..component.astrbot_compat import filter, AstrMessageEvent, command_group

from ..component import character as charmod
from ..component import dice as dice_mod
from ..component.output import get_output
from ..component.platform_adapter import get_adapter


class CharacterMixin:

    async def get_roll_display_name(self, event) -> str:
        """掷骰/检定输出中的称呼：优先当前人物卡名，没有人物卡时回退平台昵称。"""
        name = charmod.get_current_character_name(event.get_sender_id())
        if name:
            return name
        try:
            adapter = get_adapter(event)
            return await adapter.get_nickname(event)
        except Exception:
            return event.get_sender_name()

    @filter.command("st")
    async def status(self, event: AstrMessageEvent, attributes: str = None, exp: str = None):
        """人物卡属性更新 / 掷骰"""
        if not attributes:
            return

        user_id = event.get_sender_id()
        chara_id = charmod.get_current_character_id(user_id)
        is_new_character = False
        if not chara_id:
            # 直接使用 .st 时，以当前平台昵称自动创建一张默认人物卡
            default_name = charmod.sanitize_name(event.get_sender_name() or "调查员")
            existing_names = charmod.get_all_characters(user_id)
            chara_id = charmod.get_or_create_character(user_id, default_name)
            is_new_character = default_name not in existing_names

        chara_data = charmod.load_character(user_id, chara_id)
        if not chara_data:
            yield event.plain_result(get_output("pc.show.load_fail", id=chara_id))
            return

        created_note = ""
        if is_new_character:
            created_note = get_output("pc.create.default", name=chara_data.get("name", ""))

        full_expr = (str(attributes) if attributes else "") + (str(exp) if exp else "")
        attributes_clean = re.sub(r'\s+', '', full_expr)

        # 批量解析：
        # 1. 优先用已有属性名匹配，避免 "sansan值理智理智值" 这类连续无数字属性被合并；
        # 2. 未知属性名用通用规则匹配，因此 .st 也可以顺便新增属性；
        # 3. 只有解析出多于 1 个属性时才走批量逻辑，单个属性仍完全保留原逻辑。
        known_keys = sorted(chara_data["attributes"].keys(), key=len, reverse=True)
        known_pattern = re.compile(
            r"(" + "|".join(re.escape(k) for k in known_keys) + r")\s*([+\-*]?)\s*(\d+(?:d\d+)?|\d*)"
        ) if known_keys else None
        generic_pattern = re.compile(
            r"([\u4e00-\u9fa5a-zA-Z]+)\s*([+\-*]?)\s*(\d+(?:d\d+)?|\d*)"
        )

        tokens = []
        pos = 0
        while pos < len(attributes_clean):
            match = known_pattern.match(attributes_clean, pos) if known_pattern else None
            if not match:
                match = generic_pattern.match(attributes_clean, pos)
            if not match:
                tokens = []
                break
            tokens.append((match.group(1), match.group(2) or None, match.group(3) or None))
            pos = match.end()

        # 多个属性：批量更新，并允许新增属性
        if len(tokens) > 1:
            responses = []
            for attribute, operator, value_expr in tokens:
                if attribute not in chara_data["attributes"]:
                    chara_data["attributes"][attribute] = 0

                current_value = chara_data["attributes"][attribute]
                value_num = 0
                roll_detail = ""

                if value_expr and 'd' in value_expr.lower():
                    dice_match = re.match(r"(\d*)d(\d+)", value_expr.lower())
                    if dice_match:
                        dice_count = int(dice_match.group(1)) if dice_match.group(1) else 1
                        dice_faces = int(dice_match.group(2))
                        rolls = dice_mod.roll_dice(dice_count, dice_faces)
                        value_num = sum(rolls)
                        roll_detail = get_output(
                            "dice.detail",
                            detail=f"[{' + '.join(map(str, rolls))}] = {value_num}"
                        )
                elif value_expr:
                    try:
                        value_num = int(value_expr)
                    except ValueError:
                        yield event.plain_result(get_output("pc.show.invalid_value", value=value_expr))
                        return

                if operator == "+":
                    new_value = current_value + value_num
                elif operator == "-":
                    new_value = current_value - value_num
                elif operator == "*":
                    new_value = current_value * value_num
                else:
                    new_value = value_num

                chara_data["attributes"][attribute] = max(0, new_value)
                response = get_output(
                    "pc.update.success",
                    attr=attribute,
                    old=current_value,
                    new=new_value
                )
                if roll_detail:
                    response += "\n" + roll_detail
                responses.append(response)

            charmod.save_character(user_id, chara_id, chara_data)
            response = "\n".join(responses)
            if created_note:
                response = created_note + "\n" + response
            await self.save_log(group_id=event.get_group_id(), content=response)
            yield event.plain_result(response)
            return

        # 下面保持原有单属性逻辑不变
        match = re.match(r"([\u4e00-\u9fa5a-zA-Z]+)\s*([+\-*]?)\s*(\d+(?:d\d+)?|\d*)", attributes_clean)
        if not match:
            yield event.plain_result(get_output("pc.show.attr_missing", attribute=attributes_clean))
            return

        attribute = match.group(1)
        operator = match.group(2) if match.group(2) else None
        value_expr = match.group(3) if match.group(3) else None

        if attribute not in chara_data["attributes"]:
            yield event.plain_result(get_output("pc.show.attr_missing", attribute=attribute))
            return

        current_value = chara_data["attributes"][attribute]

        value_num = 0
        roll_detail = ""
        if value_expr and 'd' in value_expr.lower():
            dice_match = re.match(r"(\d*)d(\d+)", value_expr.lower())
            if dice_match:
                dice_count = int(dice_match.group(1)) if dice_match.group(1) else 1
                dice_faces = int(dice_match.group(2))
                rolls = dice_mod.roll_dice(dice_count, dice_faces)
                value_num = sum(rolls)

                roll_detail = get_output("dice.detail", detail=f"[{' + '.join(map(str, rolls))}] = {value_num}")
        elif value_expr:
            try:
                value_num = int(value_expr)
            except ValueError:
                yield event.plain_result(get_output("pc.show.invalid_value", value=value_expr))
                return

        if operator == "+":
            new_value = current_value + value_num
        elif operator == "-":
            new_value = current_value - value_num
        elif operator == "*":
            new_value = current_value * value_num
        else:
            new_value = value_num

        chara_data["attributes"][attribute] = max(0, new_value)
        charmod.save_character(user_id, chara_id, chara_data)

        response = get_output("pc.update.success", attr=attribute, old=current_value, new=new_value)
        if roll_detail:
            response += "\n" + roll_detail
        if created_note:
            response = created_note + "\n" + response

        await self.save_log(group_id=event.get_group_id(), content=response)

        yield event.plain_result(response)

    @command_group("pc")
    def pc(self):
        pass

    # ----------------- pc create -----------------
    @pc.command("create")
    async def pc_create_character(self, event, name: Optional[str] = None, attributes: str = ""):
        user_id = event.get_sender_id()
        user_name = event.get_sender_name()
        characters = charmod.get_all_characters(user_id)

        name = charmod.sanitize_name(name or user_name or "调查员")

        if name in characters:
            yield event.plain_result(get_output("pc.create.duplicate", name=name))
            return

        attributes_dict = charmod.default_attributes()

        matches = re.findall(r"([\u4e00-\u9fa5a-zA-Z]+)(\d+)", attributes)
        for attr, val in matches:
            attributes_dict[attr] = int(val)

        attributes_dict['max_hp'] = (attributes_dict.get('siz', 0) + attributes_dict.get('con', 0)) // 10
        attributes_dict['max_san'] = attributes_dict.get('pow', 0)

        chara_id = charmod.create_character(user_id, name, attributes_dict)
        response = get_output("pc.create.success", name=name, id=chara_id)

        yield event.plain_result(response)
        await self.save_log(group_id=event.get_group_id(), content=response)

    # ----------------- pc show -----------------
    @pc.command("show")
    async def pc_show_character(self, event, attribute_name: Optional[str] = None):
        user_id = event.get_sender_id()
        chara_id = charmod.get_current_character_id(user_id)

        if not chara_id:
            yield event.plain_result(get_output("pc.show.no_active"))
            return

        chara_data = charmod.load_character(user_id, chara_id)
        if not chara_data:
            yield event.plain_result(get_output("pc.show.load_fail", id=chara_id))
            return

        if attribute_name:
            if attribute_name not in chara_data["attributes"]:
                yield event.plain_result(get_output("pc.show.attr_missing", attribute=attribute_name))
                return
            val = chara_data["attributes"][attribute_name]
            yield event.plain_result(get_output("pc.show.attr", attr=attribute_name, value=val))
        else:
            attributes = "\n".join([f"{key}: {value}" for key, value in chara_data["attributes"].items()])
            yield event.plain_result(get_output("pc.show.all", name=chara_data['name'], attributes=attributes))

    # ----------------- pc list -----------------
    @pc.command("list")
    async def pc_list_characters(self, event):
        user_id = event.get_sender_id()
        characters = charmod.get_all_characters(user_id)
        if not characters:
            yield event.plain_result(get_output("pc.list.empty"))
            return

        current = charmod.get_current_character_id(user_id)
        chara_list = "\n".join([f"- {name} (ID: {ch}) {'(当前)' if ch == current else ''}" for name, ch in characters.items()])
        yield event.plain_result(get_output("pc.list.result", list=chara_list))

    # ----------------- pc change -----------------
    @pc.command("change")
    async def pc_change_character(self, event, name: str):
        user_id = event.get_sender_id()
        characters = charmod.get_all_characters(user_id)
        if name not in characters:
            yield event.plain_result(get_output("pc.change.missing", name=name))
            return

        charmod.set_current_character(user_id, characters[name])
        yield event.plain_result(get_output("pc.change.success", name=name))

    # ----------------- pc update -----------------
    @pc.command("update")
    async def pc_update_character(self, event, attribute: str, value: str):
        user_id = event.get_sender_id()
        chara_id = charmod.get_current_character_id(user_id)
        if not chara_id:
            yield event.plain_result(get_output("pc.update.no_active"))
            return

        chara_data = charmod.load_character(user_id, chara_id)
        if attribute not in chara_data["attributes"]:
            chara_data["attributes"][attribute] = 0

        current_value = chara_data["attributes"][attribute]
        match = re.match(r"([+\-*]?)(\d*)[dD]?(\d*)", value)
        if not match:
            yield event.plain_result(get_output("pc.update.error_format"))
            return

        operator = match.group(1)
        dice_count = int(match.group(2)) if match.group(2) else 1
        dice_faces = int(match.group(3)) if match.group(3) else 0

        if dice_faces > 0:
            rolls = [random.randint(1, dice_faces) for _ in range(dice_count)]
            value_num = sum(rolls)
            roll_detail = f"掷骰结果: [{' + '.join(map(str, rolls))}] = {value_num}"
        else:
            value_num = int(match.group(2)) if match.group(2) else 0
            roll_detail = ""

        if operator == "+":
            new_value = current_value + value_num
        elif operator == "-":
            new_value = current_value - value_num
        elif operator == "*":
            new_value = current_value * value_num
        else:
            new_value = value_num

        chara_data["attributes"][attribute] = max(0, new_value)
        charmod.save_character(user_id, chara_id, chara_data)

        text = get_output("pc.update.success", attr=attribute, old=current_value, new=new_value)
        if roll_detail:
            text = text + "\n" + roll_detail
        await self.save_log(group_id=event.get_group_id(), content=text)
        yield event.plain_result(text)

    # ----------------- pc delete -----------------
    @pc.command("delete")
    async def pc_delete_character(self, event, name: str):
        user_id = event.get_sender_id()
        success, _ = charmod.delete_character(user_id, name)
        if not success:
            yield event.plain_result(get_output("pc.delete.fail", name=name))
            return
        yield event.plain_result(get_output("pc.delete.success", name=name))

    # ----------------- filter sn -----------------
    @filter.command("sn")
    async def filter_set_nickname(self, event):
        adapter = get_adapter(event)
        if not adapter.supports_group_card:
            yield event.plain_result(get_output("nick.platform_unsupported"))
            return

        chara_id = charmod.get_current_character_id(event.get_sender_id())
        chara_data = charmod.load_character(event.get_sender_id(), chara_id)
        if not chara_data:
            yield event.plain_result(get_output("nick.no_character", id=chara_id))
            return

        max_hp = (chara_data['attributes'].get('con', 0) + chara_data['attributes'].get('siz', 0)) // 10
        name = chara_data['name']
        hp = chara_data['attributes'].get('hp', 0)
        san = chara_data['attributes'].get('san', 0)
        dex = chara_data['attributes'].get('dex', 0)
        new_card = f"{name} HP:{hp}/{max_hp} SAN:{san} DEX:{dex}"

        await adapter.set_group_card(event, new_card)
        yield event.plain_result(get_output("nick.success"))
