import editJsonFile from "edit-json-file";
import { readFile, writeFile } from "fs/promises";
import { glob } from "glob";

const PARAMS_PATH = import.meta.dirname + "/../params.json"
const SCHEMA_PATH = import.meta.dirname + "/../schema.json"
const PARAM_PATH_IN_SCHEMA = "properties.pids.items.properties.parameters.propertyNames.enum";

let params = null;

export async function process_params(){
    const params = await get_params();
    let param_array = Object.getOwnPropertyNames(params);
    let schema_file = editJsonFile(SCHEMA_PATH);

    let existing_params = schema_file.get(PARAM_PATH_IN_SCHEMA);
    if (existing_params !== param_array) {
        schema_file.set(PARAM_PATH_IN_SCHEMA, param_array);
        schema_file.save();
    }

    await save_params_md();
}

export async function get_params(){
    if(params === null){
        params = JSON.parse(await readFile(PARAMS_PATH));
    }
    return params;
}

async function save_params_md(){

    // Generate Supported_Parameters.md
const supportedParametersListFilepath = await glob("../docs/content/*.Config/*.Automate/*.Supported_Parameters.md");
let supportedParametersTargetPath = null;
if(supportedParametersListFilepath.length !== 1){
    throw new Error("We are expecting A supported paramaters MD in Config/Automate")
}
supportedParametersTargetPath = supportedParametersListFilepath[0];

let supportedParametersContent = await readFile('src/md/supported-params.md');
const params = await get_params();
Object.getOwnPropertyNames(params).forEach((param) => {
    const entry = params[param];
    const desc = entry.description || "";
    // Format settings as JSON, but compact
    const settings = entry.settings
        ? Object.entries(entry.settings)
            .map(([k, v]) => `${k}: ${v}`)
            .join(", ")
        : "";
    supportedParametersContent += `| \`${param}\` | ${desc} | ${settings} |\n`;
    });
    
    await writeFile(supportedParametersTargetPath, supportedParametersContent);
}