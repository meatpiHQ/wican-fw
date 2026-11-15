import Ajv from "ajv";
import { readFile } from "fs/promises";
import { glob } from "glob";

const ajv = new Ajv();
const schema = JSON.parse(await readFile("schema.json", "utf-8"));

// The names a profile may use are the entries of params.json. The copy of that
// list in schema.json is written by the build (src/params.js), so it is one
// build behind in a change that adds a parameter together with a profile that
// uses it. Take the names from params.json, or such a change is refused here
// before the build can refresh the list.
const params = JSON.parse(await readFile("params.json", "utf-8"));
schema.properties.pids.items.properties.parameters.propertyNames.enum =
  Object.getOwnPropertyNames(params);

const validate = ajv.compile(schema);

const source_folder = "../vehicle_profiles";
const files = await glob(source_folder + "/**/*.json");

let errors = 0;

async function validate_profile(path) {
  let file = await readFile(path, "utf-8");
  console.log("Parsing: " + path);
  let data = JSON.parse(file);
  let valid = validate(data);
  if (valid) return;
  console.log(validate.errors);
  errors++;
}

let promises = [];
files.forEach((file) => {
  promises.push(validate_profile(file));
});

await Promise.all(promises);

if (errors > 0) {
  throw new Error("Validation issues found, see log for details");
}
