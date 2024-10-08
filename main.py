import os
from flask import Flask, render_template, request, redirect, url_for, flash, session as flask_session, g, jsonify
from config.supabase_client import supabase
from dotenv import load_dotenv
from config.usertables import get_user_team, update_coins, update_points, remove_player, add_player, insert_user_table
from config.matchday import process_games
from config.resetpoints import resetpoints
from config.auth import postregister, postlogin
from config.leaderboard import overall_leaderboard, matchday_leaderboard
load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ.get('FLASK_SECRET_KEY')
current_matchday = 1

# runs before every request to get user info
@app.before_request
def load_user_info():
    g.user_info = flask_session.get('user_info', None)

# make user info globally available in all templates
@app.context_processor
def inject_user_info():
    return dict(user_info=g.user_info)

# default route for landing page
@app.route('/')
def home():
    print("flask session")
    print(flask_session)
    print("end")
    global current_matchday
    # 518295b1-b9c0-41b0-9748-b2d876d3655f
    return render_template('index.html', matchday=current_matchday)

@app.route('/increment-matchday', methods=['POST'])
def increment_matchday():
    global current_matchday
    try:
        process_games(current_matchday)
        current_matchday += 1
        return jsonify({'matchday': current_matchday})
    except Exception as e:
        # Log the exception to the console
        print(f"Error incrementing matchday: {e}")
        return jsonify({'error': 'Internal Server Error'}), 500

@app.route('/reset-matchday', methods=['POST'])
def reset_matchday():
    global current_matchday
    current_matchday = 1

    try:
        # Reset points in the database
        resetpoints()

        # Reset points in the session if the user is logged in
        if 'user_info' in flask_session:
            flask_session['user_info']['user_team']['points_matchday'] = 0
            flask_session['user_info']['user_team']['total_points'] = 0
            flask_session.modified = True

        return jsonify({"message": "Matchday points reset successfully", "matchday": current_matchday}), 200
    except Exception as e:
        print(f"An error occurred: {e}")
        return jsonify({"message": "Internal server error"}), 500

# route for playerstats 
@app.route('/playerstats')
def playerstats():
    response = supabase.table('players').select('*').execute()
    player_data = response.data if response.data else []
    return render_template('pages/playerstats.html', players=player_data)

# route for leaderboards page
@app.route('/leaderboards')
def leaderboards():
    overall = overall_leaderboard()
    matchday = matchday_leaderboard()
    return render_template('pages/leaderboards.html', overall_leaderboard=overall, matchday_leaderboard=matchday)

# route for gameschedule page
@app.route('/gameschedule')
def gameschedule():
    return render_template('pages/gameschedule.html')

# Route for myteam page
@app.route('/myteam')
def myteam():
    app.logger.debug('Session: %s', flask_session)
    if 'user_info' not in flask_session:
        return redirect(url_for('login'))

    uid = flask_session['user_info'].get('uid')
    return redirect(url_for('show_team', user_id=uid))

# Route that appends uid to the end of the URL
@app.route('/myteam/<user_id>')
def show_team(user_id):
    user_team = get_user_team(user_id)

    if not user_team:
        return redirect(url_for('home'))

    if isinstance(user_team, list) and len(user_team) > 0:
        team = user_team[0]  # Get team record
        # print("now printing my team", team)

        position_keys = [
            'guard',
            'center',
            'forward_center',
            'forward',
            'guard_forward',
            'forward_guard'
        ]

        # Fetch player details for each position
        players = {}
        teams = {}
        for position in position_keys:
            player_id = team.get(position)
            if player_id:
                player_response = supabase.from_('playervalues').select('*').eq('person_id', player_id).single().execute()
                if player_response.data:
                    players[position] = player_response.data
                    team_id = player_response.data['team_id']
                    # Fetch team data for the player's team
                    if team_id not in teams:
                        team_response = supabase.from_('teamdata').select('*').eq('team_id', team_id).single().execute()
                        if team_response.data:
                            teams[team_id] = team_response.data


        # print()
        # print()
        # print()
        # print("printing players", players)
        # print("printing teams", teams) 

        return render_template(
            'pages/myteam.html',
            team=team,
            players=players,
            teams=teams
        )
    else:
        return redirect(url_for('home'))

# Function to retrieve corresponding user team
def get_user_team(uid):
    try:
        response = supabase.from_('user_teams').select('*').eq('uid', uid).execute()
    except Exception as e:
        flash("Access token expired. Please login again.", "font-semibold text-red-500")
        return redirect(url_for('login')) # Redirect to login.html
    
    if response and response.data:
        return response.data
    else:
        return None

# route for playershop page
@app.route('/playershop')
def playershopteams():
    response = supabase.table('teamdata').select('*').execute()
    team_data = response.data if response.data else []
    # print(team_data)
    return render_template('pages/playershopteams.html', teams=team_data)

# route for players of a specific team
@app.route('/playershop/<team_id>')
def playershop_team(team_id):
    # Fetch team data
    team_response = supabase.table('teamdata').select('*').eq('team_id', team_id).execute()
    team_data = team_response.data[0] if team_response.data else None

    # Fetch players for the specific team
    players_response = supabase.table('playervalues').select('*').eq('team_id', team_id).execute()
    players_data = players_response.data if players_response.data else []

    return render_template('pages/playershopplayers.html', team=team_data, players=players_data)


# logic to add player to team ( IN PROGRESS )
@app.route('/add-to-team', methods=['POST'])
def add_to_team():
    data = request.json
    player_id = data.get('playerId')
    player_position = data.get('playerPosition').lower()  # Convert to lowercase
    player_price = float(data.get('playerPrice'))
    user_id = flask_session.get('user_info', {}).get('uid')

    app.logger.debug(f'Received request to add player: {player_id}, Position: {player_position}, Price: {player_price}, User ID: {user_id}')

    if not user_id:
        app.logger.error('User not authenticated')
        return jsonify({'message': 'User not authenticated'}), 401

    try:
        # Fetch the user's current team and coins
        user_response = supabase.from_('user_teams').select('*').eq('uid', user_id).single().execute()
        app.logger.debug(f'User team response: {user_response}')

        if not user_response.data:
            app.logger.error('User team not found')
            return jsonify({'message': 'User team not found'}), 404

        user_team = user_response.data
        current_coins = user_team.get('coins', 0)
        current_points = user_team.get('total_points', 0)
        app.logger.debug(f'Current coins: {current_coins}, Current points: {current_points}')

        # Check if the user can afford the player
        if current_coins < player_price:
            app.logger.error('Not enough coins')
            return jsonify({'message': 'Not enough coins'}), 400

        # Update the user's team and coins
        updated_coins = current_coins - player_price
        updated_points = current_points 
        update_data = {
            player_position: player_id,
            'coins': updated_coins
        }

        response = supabase.from_('user_teams').update(update_data).eq('uid', user_id).execute()
        app.logger.debug(f'Update response: {response}')

        if response.data:
            # Update the session with the new coin balance and team
            flask_session['user_info']['user_team'][player_position] = player_id
            flask_session['user_info']['user_team']['coins'] = updated_coins
            flask_session['user_info']['user_team']['total_points'] = updated_points

            # Ensure the session is saved
            flask_session.modified = True

            return jsonify({
                'message': 'Player added to your team successfully',
                'new_balance': {
                    'coins': updated_coins,
                    'points': updated_points
                }
            }), 200
        else:
            app.logger.error('Failed to add player to your team')
            return jsonify({'message': 'Failed to add player to your team', 'details': response.error}), 400

    except Exception as e:
        app.logger.error(f'Error adding player to team: {e}')
        return jsonify({'message': 'Internal server error'}), 500

# register, login, and logout
@app.route('/register')
def register():
    return render_template('pages/register.html')

@app.route('/login')
def login():
    return render_template('pages/login.html')

@app.route('/postregister', methods=['POST'])
def handlepostregister():
    return postregister()

@app.route('/postlogin', methods=['POST'])
def handlepostlogin():
    return postlogin()

@app.route('/logout')
def logout():
    flask_session.pop('user_info', None)
    return redirect(url_for('home'))    

if __name__ == '__main__':
    app.run(debug=True)