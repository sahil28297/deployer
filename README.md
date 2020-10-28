# Deployer

Deploy test instances on [DigitalOcean](https://www.digitalocean.com) by commenting on a PR on [GitHub](https://github.com). 


## Prerequisite
- [DigitalOcean](https://www.digitalocean.com) account and DigitalOcean access token.
- Snapshot ID of a droplet which will be used as base image of the test instance.
- [GitHub](https://github.com) credentials and an  access token with 'repo' access.


## Setup

- Setup a [Frappe Bench](https://github.com/frappe/bench) environment and install this app to your site.

- Set the webhook URL in GitHub to point to this method:
    ```
    https://[hostname]/api/method/deployer.deployer.doctype.deployer_instance.deploy_handler.handle_event
    ```
    and content_type as ```application/json```

- Add the webhook secret to common_site_config.json as:
    ```
    {
        "deployer_secret": "your-webhook-secret-key"
    }
    ```
    ensure that the secret key is the same as the one provided to GitHub


## Site Configurations
Login to the site where deployer app is installed.
Open `Deployer Config` doctype and fill in the fields accordingly and click on `Save`.

![deployer_config](https://user-images.githubusercontent.com/37302950/100335406-f98c8f00-2ffa-11eb-808d-a3087a958e51.png)

- Max Instances: Maximum number of test instances that can be active at any particular time (eg.: `10`).

- Allowed Requesters: List of GitHub username who are allowed to create a test instance. Any other user commenting on the PR won't trigger the deployer.
eg.: 
    ```
    sahil28297
    thunderbottom
    .....
    .....
    ```

- Bot Username: GitHub username of the bot that will be used in the command to trigger the deploy.

- Bot Password: GitHub password of the above mentioned bot.

- GitHub Access Token: GitHub Access Token of the above mentioned bot with `repo` access.

- Branch Whitelist: List of branches on GitHub where the PRs are sent. Deployer will trigger a deploy only for the PRs that are sent on these whitelisted branches.

- Digital Ocean Access Token: API key for the DigitalOcean account.

- Digital Ocean Snapshot ID: snapshot id of the snapshot/image which will be used as the base image of the test instance.

## Usage
- Open the PR on your browser for which the test instance is to be deployed.
- Comment on the PR the following command ```@bot_username create instance``` where bot_username is the GitHub username of the bot entered in the `Deployer Config` DocType.
    ![Screenshot from 2020-11-26 16-00-30](https://user-images.githubusercontent.com/37302950/100339981-8d148e80-3000-11eb-8049-4afb5ed271dd.png)
- An instance will be created on DigitalOcean under the default Project. The deployer will then pull the changes in your PR on the test instance, run `bench setup requirements`, `bench build`, `bench migrate`, `bench restart` on that bench, and add a check to your PR on GitHub, clicking on 'details' will redirect you to the created instance on your browser.
    ![Screenshot from 2020-11-26 16-01-51](https://user-images.githubusercontent.com/37302950/100340968-f3e67780-3001-11eb-97e0-8b80dc415b85.png)


### Built using [Frappe Framework](https://frappeframework.com)

### License

MIT