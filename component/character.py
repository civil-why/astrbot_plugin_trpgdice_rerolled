import os
import json
import uuid
import random
import re

from .output import get_output

# 人物卡名称：仅允许中英文、数字、空格、下划线和连字符，最长 40 字
_NAME_PATTERN = re.compile(r'^[\u4e00-\u9fa5a-zA-Z0-9 _\-]{1,40}$')
# 属性名：同上，最长 20 字
_ATTR_PATTERN = re.compile(r'^[\u4e00-\u9fa5a-zA-Z0-9_]{1,20}$')

PLUGIN_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FOLDER = os.path.join(PLUGIN_DIR, "..", "chara_data")

def get_user_folder(user_id: str):
    """
    获取用户的人物卡存储文件夹路径，不存在则自动创建。
    """
    folder = os.path.join(DATA_FOLDER, str(user_id))
    os.makedirs(folder, exist_ok=True)
    return folder

def get_all_characters(user_id: str):
    """
    获取用户所有人物卡，返回 {人物卡名: 人物卡id} 字典。
    """
    folder = get_user_folder(user_id)
    characters = {}
    for filename in os.listdir(folder):
        if filename.endswith(".json"):
            path = os.path.join(folder, filename)
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                characters[data["name"]] = data["id"]
    return characters

def get_character_file(user_id: str, chara_id: str):
    """
    获取指定人物卡的文件路径。
    """
    return os.path.join(get_user_folder(user_id), f"{chara_id}.json")

def get_current_character_file(user_id: str):
    """
    获取当前选中人物卡的记录文件路径。
    """
    return os.path.join(get_user_folder(user_id), "current.txt")

def get_current_character_id(user_id: str):
    """
    获取当前选中的人物卡ID。
    """
    path = get_current_character_file(user_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    return None

def get_current_character(user_id: str):
    """
    获取当前选中的人物卡数据（字典），没有则返回None。
    """
    chara_id = get_current_character_id(user_id)
    if not chara_id:
        return None
    return load_character(user_id, chara_id)

def set_current_character(user_id: str, chara_id: str):
    """
    设置当前选中的人物卡ID，写入current.txt。
    """
    with open(get_current_character_file(user_id), "w", encoding="utf-8") as f:
        f.write(chara_id if chara_id is not None else "")

def load_character(user_id: str, chara_id: str):
    """
    加载指定人物卡的数据（字典），不存在则返回None。
    """
    path = get_character_file(user_id, chara_id)
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return None

def save_character(user_id: str, chara_id: str, data: dict):
    """
    保存人物卡数据到文件，并在写回前把常见同义词组同步更新。
    同义词组中任意一个字段在某个容器（data 或其子 dict）存在时，
    将把该容器内所有同义词字段更新为该出现字段的值。
    """
    path = get_character_file(user_id, chara_id)

    # 同义词组（每组第一个为“代表/优先检查字段”，但最终会把组内所有字段设为同一值）
    SYNONYMS = [
        ["力量", "str"],
        ["敏捷", "dex"],
        ["意志", "pow"],
        ["体质", "con"],
        ["外貌", "app"],
        ["教育", "知识", "edu"],
        ["体型", "siz"],
        ["智力", "灵感", "int"],
        ["san", "san值", "理智", "理智值"],
        ["幸运", "运气"],
        ["mp", "魔法"],
        ["hp", "体力", "max_hp"],
        ["max_san"],

        # 技能/替代名（根据你给出的列表合并）
        ["计算机", "计算机使用", "电脑"],
        ["会计"],
        ["人类学"],
        ["估价"],
        ["考古学"],
        ["取悦"],
        ["攀爬"],
        ["电脑", "计算机"],  # 重复安全
        ["信用", "信誉", "信用评级"],
        ["克苏鲁", "克苏鲁神话", "cm"],
        ["乔装"],
        ["闪避"],
        ["汽车", "驾驶", "汽车驾驶"],
        ["电气维修"],
        ["电子学"],
        ["话术"],
        ["斗殴"],
        ["手枪"],
        ["急救"],
        ["历史"],
        ["恐吓"],
        ["跳跃"],
        ["拉丁语"],
        ["母语"],
        ["法律"],
        ["图书馆", "图书馆使用"],
        ["聆听"],
        ["开锁", "撬锁", "锁匠"],
        ["机械维修"],
        ["医学"],
        ["博物学", "自然学"],
        ["领航", "导航"],
        ["神秘学"],
        ["重型操作", "重型机械", "操作重型机械", "重型"],
        ["说服"],
        ["精神分析"],
        ["心理学"],
        ["骑术"],
        ["妙手"],
        ["侦查"],
        ["潜行"],
        ["生存"],
        ["游泳"],
        ["投掷"],
        ["追踪"],
        ["驯兽"],
        ["潜水"],
        ["爆破"],
        ["读唇"],
        ["催眠"],
        ["炮术"],
        ["max_hp"],  # 已包含在 hp 组，但再列一次无伤
        ["max_san"],  # 同上
    ]

    # helper: 给单个容器（dict）同步同义词组
    def sync_container(container: dict):
        if not isinstance(container, dict):
            return
        for group in SYNONYMS:
            # 查找组内在容器中存在的键（保留顺序）
            present_keys = [k for k in group if k in container]
            if not present_keys:
                continue
            # 以第一个出现的键的值作为统一值
            value = container[present_keys[0]]
            # 将组中所有键都写回该值（无则新增）
            for k in group:
                container[k] = value

    # 对主 data 同步
    sync_container(data)

    # 对 data 下的所有直接子 dict 也同步（常见的 attributes/skills 等）
    for k, v in list(data.items()):
        if isinstance(v, dict):
            sync_container(v)

    # 最后写回文件
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)


def get_skill_value(user_id: str, skill_name: str):
    """
    获取当前选中人物卡的某项技能值，不存在则返回0。
    """
    chara_data = get_current_character(user_id)
    if not chara_data or skill_name not in chara_data["attributes"]:
        return 0
    return chara_data["attributes"][skill_name]

def sanitize_name(name: str) -> str:
    """清理人物卡名称，去掉非法字符并截断至 40 字。"""
    # 去掉路径分隔符等危险字符
    name = re.sub(r'[/\\:*?"<>|\x00-\x1f]', '', name).strip()
    return name[:40] or "调查员"


def is_valid_attr_name(attr: str) -> bool:
    """属性名只允许中英文和数字，长度 1-20。"""
    return bool(_ATTR_PATTERN.match(attr))


def create_character(user_id: str, name: str, attributes: dict):
    """
    创建新人物卡，自动生成唯一ID，并设为当前人物卡。
    返回新人物卡ID。
    """
    name = sanitize_name(name)
    # 过滤非法属性名，属性值限制在 [0, 9999]
    safe_attrs = {
        k: max(0, min(int(v), 9999)) if isinstance(v, (int, float)) else v
        for k, v in attributes.items()
        if isinstance(k, str) and is_valid_attr_name(k)
    }
    chara_id = str(uuid.uuid4())
    data = {"id": chara_id, "name": name, "attributes": safe_attrs}
    save_character(user_id, chara_id, data)
    set_current_character(user_id, chara_id)
    return chara_id

# 默认人物卡属性（与 .pc create 的初始值保持一致）
DEFAULT_ATTRIBUTES_DATA = (
    "力量0str0敏捷0dex0意志0pow0体质0con0外貌0app0教育0知识0edu0"
    "体型0siz0智力0灵感0int0san0san值0理智0理智值0幸运0运气0mp0魔法0hp0"
    "体力0会计5人类学1估价5考古学1取悦15攀爬20计算机5计算机使用5电脑5"
    "信用0信誉0信用评级0克苏鲁0克苏鲁神话0cm0乔装5闪避0汽车20驾驶20汽车驾驶20"
    "电气维修10电子学1话术5斗殴25手枪20急救30历史5恐吓15跳跃20拉丁语1母语0"
    "法律5图书馆20图书馆使用20聆听20开锁1撬锁1锁匠1机械维修10医学1博物学10"
    "自然学10领航10导航10神秘学5重型操作1重型机械1操作重型机械1重型1说服10"
    "精神分析1心理学10骑术5妙手10侦查25潜行20生存10游泳20投掷20追踪10驯兽5"
    "潜水1爆破1读唇1催眠1炮术1"
)


def default_attributes() -> dict:
    """生成默认人物卡的初始属性字典。"""
    attributes = {
        attr: int(value)
        for attr, value in re.findall(r"([\u4e00-\u9fa5a-zA-Z]+)(\d+)", DEFAULT_ATTRIBUTES_DATA)
    }
    attributes["max_hp"] = (attributes.get("siz", 0) + attributes.get("con", 0)) // 10
    attributes["max_san"] = attributes.get("pow", 0)
    return attributes


def get_current_character_name(user_id: str):
    """当前人物卡的名字，没有人物卡时返回 None。"""
    chara_data = get_current_character(user_id)
    if not chara_data:
        return None
    name = str(chara_data.get("name") or "").strip()
    return name or None


def get_or_create_character(user_id: str, name: str) -> str:
    """按名字获取人物卡并设为当前卡；同名卡不存在时用默认属性新建。

    用于直接使用 ``.st`` 时自动以平台昵称建卡。
    """
    name = sanitize_name(name or "调查员")
    characters = get_all_characters(user_id)
    if name in characters:
        chara_id = characters[name]
        set_current_character(user_id, chara_id)
        return chara_id
    return create_character(user_id, name, default_attributes())

def delete_character(user_id: str, name: str):
    """
    删除指定名字的人物卡。
    若删除的是当前人物卡，则清空current.txt。
    返回 (是否成功, 被删除人物卡ID)。
    """
    characters = get_all_characters(user_id)
    chara_id = get_current_character_id(user_id)
    if name not in characters:
        return False, None
    chara_to_delete_id = characters[name]
    path = get_character_file(user_id, chara_to_delete_id)
    try:
        os.remove(path)
    except FileNotFoundError:
        return False, None
    if chara_to_delete_id == chara_id:
        set_current_character(user_id, None)
    return True, chara_to_delete_id

def set_nickname(user_id: str, chara_id: str, nickname: str):
    """
    为指定人物卡设置昵称。
    """
    chara = load_character(user_id, chara_id)
    if chara:
        chara["nickname"] = nickname
        save_character(user_id, chara_id, chara)
        return True
    
    
def grow_up(user_id: str, skill_name: str, skill_value: int = None):
    """
    技能成长判定（COC规则）
    不依赖 event，返回结果字符串，由调用端处理发送。
    """
    update_skill_value = False

    # 如果未提供 skill_value，则从当前人物卡读取
    if skill_value is None:
        skill_value = get_skill_value(user_id, skill_name)
        chara_id = get_current_character_id(user_id)
        chara_data = load_character(user_id, chara_id)
        update_skill_value = True
    else:
        chara_id = get_current_character_id(user_id)
        chara_data = load_character(user_id, chara_id)

    # 校验 skill_value 是否为整数
    try:
        skill_value = int(skill_value)
    except ValueError:
        return get_output("pc.show.attr_missing", skill_name=skill_name)

    # 掷骰
    tens_digit = random.randint(0, 9)
    ones_digit = random.randint(0, 9)
    roll_result = 100 if (tens_digit == 0 and ones_digit == 0) else (tens_digit * 10 + ones_digit)

    # 成长判定：roll > skill_value 或 roll > 95 成长
    if roll_result > skill_value or roll_result > 95:
        en_value = random.randint(1, 10)
        new_value = skill_value + en_value
        result = get_output("pc.grow.success", skill_name=skill_name, skill_value=skill_value, en_value=en_value, new_value = new_value)
        if update_skill_value:
            chara_data["attributes"][skill_name] = skill_value + en_value
            save_character(user_id, chara_id, chara_data)
    else:
        result = get_output("pc.grow.failure")

    # 返回完整输出
    return get_output(
        "pc.grow.boost_result",
        skill_name=skill_name,
        roll_result=roll_result,
        skill_value=skill_value,
        result=result
    )