import Ajv from "ajv"
import { readFile } from 'fs/promises';
import { glob } from 'glob'

const ajv = new Ajv()
const schema = JSON.parse(await readFile("schema.json", "utf-8"))
const validate = ajv.compile(schema)

const source_folder = '../vehicle_profiles';
const files = await glob(source_folder + '/**/*.json')

let errors = 0;

async function validate_profile(path){
    let file = await readFile(path, "utf-8");
    let data = JSON.parse(file);
    let valid = validate(data);
    if(valid) return

    console.log(path)
    console.log(validate.errors)
    errors++;
}

let promises = [];
files.forEach(file => {
    promises.push(validate_profile(file));
});

await Promise.all(promises);

if(errors > 0){
    throw new Error('Validation issues found, see log for details');
}