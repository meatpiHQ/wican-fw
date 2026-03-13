'''
Python script for porting from Torque PIDs to WiCAN json
Run as 
$ python Torque_to_WiCANjson.py <torque_filename>
The script will output two files in the directory of <torque_filename>:
<torque_filename_noext>.params.json
<torque_filename_noext>.json
<torque_filename_noext>.params.json is formatted to be merged with ../.vehicle_profiles/params.json
<torque_filename_noext>.json is formatted to be merged with ./<make>/<model>.json
This part requires care and should be done manually and/or with help from an LLM. Blindly copy-pasting will likely produce duplicate parameters and should be avoided. There is low likelihood that the ported Torque shortnames map onto existing WiCAN shortnames. Use long names to make the mapping correctly.
We use params.csv to map params from torque names to WiCAN names. The script will skip adding params entries for any Torque shortname that has a mapping, so the mapping should be updated to include any existing WiCAN params that match Torque shortnames. The mapping is case-insensitive and will ignore spaces in the header, but should otherwise be formatted as "torque,WiCAN" with no extra columns.

This script is tested on Torque PIDs in files from github.com/Esprit1st/Hyundai-Ioniq-5-Torque-Pro-PIDs/ and may need more work to import other files, but should be a good start.
'''
import numpy as np
import numpy.typing as npt
import re
import json
import sys
from pathlib import Path
import csv


# Load Torque to WiCAN name mapping from params.csv
def load_name_mapping(mapping_path):
    mapping = {}
    try:
        with open(mapping_path, newline='') as csvfile:
            reader = csv.DictReader(csvfile)
            # Fix: strip spaces from fieldnames to handle 'torque, WiCAN' header
            if reader.fieldnames:
                reader.fieldnames = [f.strip() for f in reader.fieldnames]
            for row in reader:
                # Also strip keys in row for robustness
                torque = row.get('torque', row.get('torque', '')).strip().lower()
                wican = row.get('WiCAN', row.get('WiCAN', '')).strip()
                if torque:
                    mapping[torque] = wican
    except Exception as e:
        print(f"Warning: Could not load mapping file: {e}")
    return mapping

class PID_group(object):

    def __init__(self, pid: str):
        self.pid = pid
        self.parameters = {}
        self.init = []

    def add_parameter(self, line, params: dict, name_mapping: dict):
        long_name = re.sub(r"00\d_","",line[0])
        torque_short = line[1]
        torque_short_lc = torque_short.lower()
        # Use mapping if available (case-insensitive), else fallback to formatted shortname
        if torque_short_lc in name_mapping:
            short_name = name_mapping[torque_short_lc]
        else:
            short_name = format_shortname(torque_short)
        init = line[7]
        unit = line[6] 
        expr = line[3]
        min_val = line[4]
        max_val = line[5]
        self.parameters[short_name] = process_expression(expr,self.pid)
        class_ = determine_class(long_name, unit)
        if(unit.casefold() == 'c' or unit.casefold() == 'f'):
            unit = '°'+unit
        # Only add to params if not mapped (i.e., not in mapping)
        if torque_short_lc not in name_mapping:
            params[short_name] = { "description" : long_name, "settings" : { "unit" : unit, "class" : class_, "min": min_val, "max": max_val}}
        if init not in self.init:
            self.init.append(init)

    def format_init(self):
        unique_init = np.unique(self.init)
        formatted_inits = []
        for init in unique_init:
            formatted_inits.append('ATSH'+init)
        self.formatted_init_string = ';'.join(formatted_inits)

    def make_json_dict(self):
        self.format_init()
        self.json_dict = {"pid" : self.pid, 
                          "pid_init" : self.formatted_init_string,
                          "parameters" : self.parameters}
        return self.json_dict

def format_shortname(short_name):
    short_name = re.sub(r"\s","_", short_name)
    short_name = re.sub(r"([a-z])([A-Z])",r"\1_\2", short_name)
    return short_name.upper()

def determine_class(long_name, unit):
    has_batt = re.search('battery', long_name, flags=re.IGNORECASE)
    has_curr = re.search('current', long_name)
    has_soc = re.search('state of charge', long_name, flags=re.IGNORECASE)
    has_V = re.search('voltage', long_name)
    has_power = re.search('power', long_name)
    has_temp = re.search('temperature', long_name, flags=re.IGNORECASE)
    has_press = re.search('pressure', long_name, flags=re.IGNORECASE)
    has_speed = re.search('pressure', long_name, flags=re.IGNORECASE)
    if(unit.casefold() == 'km' or unit.casefold() == 'mi'):
        return 'distance'
    elif(has_power or unit.casefold() == 'kw' or unit.casefold == 'w'):
        return 'power'
    elif(has_curr or unit.casefold() == 'a'):
        return 'current'
    elif(unit.casefold() == 'c' or unit.casefold == '°c' or unit.casefold() == 'f' or unit.casefold() == '°f' or has_temp):
        return 'temperature'
    elif(unit.casefold() == 'psi' or unit.casefold() == 'kpa' or has_press):
        return 'pressure'
    elif(unit.casefold() == 'v' or has_V or has_batt or has_soc):
        return 'battery'
    else:
        return 'none'

def process_expression(expr: str, pid: str):
    if(len(pid) == 4 or len(pid) == 5):
        return translate(expr, 0)
    elif(len(pid) == 6 or len(pid) == 7):
        return translate(expr, 1)
    else:
        raise ValueError 

def translate(expr: str, offset: int):
    print(f"Init. expression: {expr}")
    # add brackets
    expr = re.sub(r"\b(?<!:)([A-Za-z]?[A-Za-z]):([A-Za-z]?[A-Za-z])",r"[\1:\2",expr)
    expr = re.sub(r"([A-Za-z]?[A-Za-z]):([A-Za-z]?[A-Za-z])(?!:)\b",r"\1:\2]",expr)
    expr = re.sub(r"\b([A-Za-z]?[A-Za-z]):([A-Za-z]?[A-Za-z]:)+([A-Za-z]?[A-Za-z])\b",r"\1:\3",expr)
    torque_column = re.compile(r"(\b[A-Za-z]?[A-Za-z]\b)")
    cols_to_replace = torque_column.findall(expr)
    split_expression = torque_column.split(expr)
    new_expr = ''
    for part in split_expression:
        if part in cols_to_replace:
            if len(part) == 2:
                new_expr += replace_double_letters(part,part,offset)
            elif len(part) == 1:
                new_expr += replace_single_letters(part,part,offset)
        else:
            new_expr += part
    # handle casts
    new_expr = re.sub(r"SIGNED\(B([0-9]+)\)",r"(S\1)",new_expr,flags=re.IGNORECASE)
    new_expr = re.sub(r"SIGNED\(\[B([0-9]+):B([0-9]+)\]\)",r"([S\1:S\2])",new_expr,flags=re.IGNORECASE)
    new_expr = re.sub(r"INT\d+\(([][B0-9:]+)\)",r"(\1)",new_expr,flags=re.IGNORECASE)
    new_expr = re.sub(r"{(B[0-9]+:[0-7])}",r"\1",new_expr,flags=re.IGNORECASE)
    # translate bitwise operators
    new_expr = re.sub(r"<",r"<<",new_expr)
    new_expr = re.sub(r">",r">>",new_expr)
    print(f"Final expression: {new_expr}\n")
    return new_expr

def replace_single_letters(letter: str, expr: str, offset: int):
    val = ord(letter.lower())-ord('a') + offset + 4
    frame_type = (val-1) // 7 
    val += frame_type
    # re here is overkill and based on a previous attempt
    return re.sub(rf"\b{letter}\b",f"B{val}",expr)

def replace_double_letters(letters: str, expr: str, offset: int):
    val = ord(letters.lower()[1])-ord('a')+4+offset+26
    frame_type = (val-1) // 7 
    val += frame_type
    return re.sub(rf"\b{letters}\b",f"B{val}",expr)


# Main script logic
fname = sys.argv[1]
pids = np.loadtxt(fname,delimiter=',',dtype='str',comments='~',skiprows=1)

# Try to load mapping from params.csv in the same directory as this script
mapping_path = str(Path(__file__).parent / 'params.csv')
name_mapping = load_name_mapping(mapping_path)

pid_groups = []
params = {}
for line in pids:
    if("val" in line[3] or len(line[2]) == 0):
        continue
    pid = line[2].lstrip('0x') 
    pid_in_list = [group.pid == pid for group in pid_groups]
    if(any(pid_in_list)):
        group = np.array(pid_groups)[pid_in_list][0]
    else:
        group = PID_group(pid)
        pid_groups.append(group)
    group.add_parameter(line, params, name_mapping)

json_dict = {"pids" : []}
for group in pid_groups:
    json_dict["pids"].append(group.make_json_dict())

new_fname = Path(fname).with_suffix('.json')
new_params_fname = Path(fname).with_suffix('.params.json')
with open(new_fname,'w') as f:
    json.dump(json_dict, f, indent=2)
with open(new_params_fname,'w') as f:
    json.dump(params, f, indent=2)
