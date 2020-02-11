import json
from datetime import datetime

from asgiref.sync import async_to_sync
from channels.generic.websocket import WebsocketConsumer
from django.db.models import Q
from rest_framework.authtoken.models import Token

from app.models import ChatRoom, Messages, Requests, User
from app.serializers import ChatRoomSerializer, MessageSerializer

from fcm_django.models import FCMDevice


class ChatConsumer(WebsocketConsumer):


    def find_room_name(self, user, receiver, is_request_acceptor):
        # is_request_acceptor denotes whether the user trying to 
        # connect to the websocket is the one who wishes to help
        # is_request_acceptor = 1 -> user who wants to help
        # is_request_acceptor = 0 -> some other user just trying to connect to channel

        # if user is a request acceptor
        if is_request_acceptor == '1':

            # Checking if the chat room for the user already exist, 
            # if yes then connect to the same chat room
            room = ChatRoom.objects.filter(
                Q(participant1_id=user.id, participant2_id=receiver.id) |
                Q(participant1_id=receiver.id, participant2_id=user.id))
            if len(room) != 0:
                return room[0].id
            
            # Channel or chat room does not exist create a new one

            # Checking requests if it exists or not
            requset_sent = Requests.objects.filter(user_id=receiver.id)
            if len(requset_sent) == 0:
                return None 

            # Creating a new Chat Room
            room = ChatRoomSerializer(data={
                "participant1_id":user.id,
                "participant2_id":receiver.id,
                "last_message_time":datetime.now()
            })
            if room.is_valid():
                room.save()
                return room.data['id']
            else:
                return None

        # if user is not a request acceptor
        elif is_request_acceptor == '0':
            room = ChatRoom.objects.filter(
                Q(participant1_id=user.id, participant2_id=receiver.id) |
                Q(participant1_id=receiver.id, participant2_id=user.id))

            if len(room) != 0:
                return room[0].id
            else:
                return None


    def connect(self):
        # Getting values from the url
        token = self.scope['url_route']['kwargs']['token']
        is_request_acceptor = self.scope['url_route']['kwargs']['is_request_acceptor']
        receiver_id = self.scope['url_route']['kwargs']['receiver_id']

        # Checking if the user is really valid based on the token 
        # and if the receiver exists
        try:
            user = Token.objects.get(key=token).user
            receiver = User.objects.get(id=receiver_id)
            if user == receiver:
                room_id = None

            # Finding room name for the channel (Based on the chat room id)
            room_id = self.find_room_name(user, receiver, is_request_acceptor)

            print(room_id)

            if room_id != None:

                self.room_name = room_id
                self.room_group_name = 'chat_room_%s' % self.room_name
                print(self.room_group_name)

                # Join room group
                async_to_sync(self.channel_layer.group_add)(
                    self.room_group_name,
                    self.channel_name
                )               

                self.accept()
            
            else:
                self.room_group_name = None
                self.close()

        except Token.DoesNotExist:
            print("Not valid user")
            self.room_group_name = None
            self.close()  
        
        
    def disconnect(self, close_code):
        # Leave room group
        if self.room_group_name:
            async_to_sync(self.channel_layer.group_discard)(
                self.room_group_name,
                self.channel_name
            )


    # Receive message from WebSocket
    def receive(self, text_data):
        text_data_json = json.loads(text_data)

        # Getting data from the message
        message = text_data_json['message']
        sender_id = text_data_json['sender_id']
        receiver_id = text_data_json['receiver_id']

        # Saving message on database
        message_serializer = MessageSerializer(data={
            "body":message,
            "receiver_id":receiver_id,
            "sender_id":sender_id,
            "chat_room_id":self.room_name
        })

        
        if message_serializer.is_valid():
            message_serializer.save()
            # Updating the last sent message in the chat room
            try:
                room = ChatRoom.objects.get(id=self.room_name)
                room.last_message_time = datetime.now()
                room.save()

                # Sending notification to the receivers device
                user = User.objects.get(id=receiver_id)
                device = FCMDevice.objects.get(user=user)
                device.send_message(title="New Message from " + user.username, body=message)
                print("Notification sent to " + user.username + "\nBody: " + message)
            except:
                pass

        # Send message to room group
        async_to_sync(self.channel_layer.group_send)(
            self.room_group_name,
            {
                'type': 'chat_message',
                'message': message,
                'sender_id': sender_id,
                'receiver_id': receiver_id
            }
        )

    # Receive message from room group
    def chat_message(self, event):
        message = event['message']
        sender_id = event['sender_id']
        receiver_id = event['receiver_id']

        # Send message to WebSocket
        self.send(text_data=json.dumps({
            'message': message,
            'sender_id': sender_id,
            'receiver_id': receiver_id
        }))